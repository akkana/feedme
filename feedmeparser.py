#!/usr/bin/env python3

# URL parser for feedme, http://shallowsky.com/software/feedme/
# Copyright 2011-2017 by Akkana Peck.
# Share and enjoy under the GPL v2 or later.

from __future__ import print_function

import os, sys
import urllib.request, urllib.error, urllib.parse
import re
import lxml.html
from bs4 import BeautifulSoup
from http.cookiejar import CookieJar
import io
import gzip

import utils

import imagecache

# Use XDG for the config and cache directories if it's available
try:
    import xdg.BaseDirectory
except:
    pass


class NoContentError(Exception):
    pass


SKIP_NODE_PAT = r'''\s*([a-zA-Z]+)\s+(?:([a-zA-Z]+)\s*=\s*['"](.*)['"])?'''


class CookieError(Exception):
     def __init__(self, message, longmessage):
         """message is a one-line summary.
            longmessage is the traceback.fmt_exc stack trace.
         """
         self.message = message
         self.longmessage = longmessage
         # It would be nice to be able to pass the stack trace
         # in a way that could be examined (e.g. print only the
         # last message), but traceback.extract_stack() doesn't
         # survive being passed through another exception handler;
         # it would probably require making a deep copy of it.
         # For now, just pass traceback.fmt_exc().


class FeedmeURLDownloader(object):
    """An object that can download stories while retaining
       information about a feed, such as feed name, user_agent,
       encoding, cookie file and other config values.
    """

    def __init__(self, feedname, verbose=False):
        self.feedname = feedname
        self.user_agent = utils.VersionString
        self.encoding = None
        self.cookiejar = None
        self.verbose = verbose

    def download_url(self, url, referrer=None, user_agent=None):
        """Download a URL (likely http or RSS) from the web and return its
           contents as a str. Allow for possible vagaries like cookies,
           redirection, compression etc.
        """
        if not user_agent:
            user_agent = utils.VersionString

        if url.startswith("file://"):
            # In file:, allow for relative filenames even though that's not
            # part of the real file:// spec, to make testing a little easier.
            filename = url[7:]
            with open(filename) as fp:
                return fp.read()

        request = urllib.request.Request(url)

        # If we're after the single-page URL, we may need a referrer
        if referrer:
            request.add_header('Referer', referrer)

        request.add_header('User-Agent', user_agent)

        if self.verbose:
            print("download_url", url, "referrer=", referrer, \
                                "user_agent", user_agent, file=sys.stderr)

        if not self.cookiejar:
            # Create the cookiejar once per site; it will be reused
            # for all site stories fetched, but it won't be saved
            # for subsequent days.
            self.cookiejar = None
            cookiefile = utils.g_config.get(self.feedname, "cookiefile",
                                    fallback=None)
            if cookiefile:
                try:
                    cookiefile = os.path.expanduser(cookiefile)
                    # If a cookiefile was specified, use those cookies.
                    self.cookiejar = get_firefox_cookie_jar(cookiefile)
                except Exception as e:
                    errmsg = "Couldn't get cookies from file %s" % cookiefile
                    print(errmsg, file=sys.stderr)
                    raise CookieError(errmsg,
                                      traceback.format_exc()) from None

            if not self.cookiejar:
                # Allow for cookies in the request even if no cookiejar was
                # specified. Some sites, notably nytimes.com, degrade to
                # an infinite redirect loop if cookies aren't enabled.
                self.cookiejar = CookieJar()

        opener = urllib.request.build_opener(
            urllib.request.HTTPCookieProcessor(self.cookiejar))
        response = opener.open(request, timeout=20)
        # Lots of ways this can fail.
        # e.g. ValueError, "unknown url type"
        # or BadStatusLine: ''

        # At this point it would be lovely to check whether the
        # mime type is HTML or RSS. Unfortunately, all we have is a
        # httplib.HTTPMessage instance which is completely
        # undocumented (see http://bugs.python.org/issue3428).

        # It's not documented, but sometimes after urlopen
        # we can actually get a content type. If it's not
        # text/something, that's bad.
        ctype = response.headers['content-type']
        if ctype and ctype != '' and not ctype.startswith("text") \
           and not ctype.startswith("application/rss") \
           and not ctype.startswith("application/xml") \
           and not ctype.startswith("application/x-rss+xml") \
           and not ctype.startswith("application/atom+xml"):
            if self.verbose:
                print(url, "isn't text -- content-type was", \
                      ctype, ". Skipping.", file=sys.stderr)
            response.close()
            raise RuntimeError("Contents not text (%s)! %s" % (ctype, url))

        # Were we redirected? geturl() will tell us that.
        self.cur_url = response.geturl()

        # but sadly, that means we need another request object
        # to parse out the host and prefix:
        real_request = urllib.request.Request(self.cur_url)
        real_request.add_header('User-Agent', user_agent)

        # A few sites, like http://nymag.com, gzip their http.
        # urllib2 doesn't handle that automatically: we have to ask for it.
        # But some other sites, like the LA Monitor, return bad content
        # if you ask for gzip.
        if utils.g_config.getboolean(self.feedname, 'allow_gzip'):
            request.add_header('Accept-encoding', 'gzip')

        # feed() is going to need to know the host, to rewrite urls.
        # So save host and prefix based on any redirects we've had:
        # feedmeparser will need them.
        self.host = real_request.host
        self.prefix = real_request.type + '://' + self.host + '/'

        # urllib2 unfortunately doesn't read unicode,
        # so try to figure out the current encoding:
        if not self.encoding:
            if self.verbose:
                print("download_url: self.encoding not set, "
                      "getting it from headers", file=sys.stderr)
            self.encoding = response.headers.get_content_charset()
            enctype = response.headers['content-type'].split('charset=')
            # If there are multiple values, the encoding should be the last one
            if len(enctype) > 1:
                self.encoding = enctype[-1]
            else:
                if self.verbose:
                    print("No enctype; defaulting to utf-8", file=sys.stderr)
                self.encoding = 'utf-8'
            # theoatmeal sets this to  'ISO-8859-1; filename=feed.xml'
            if ';' in self.encoding:
                self.encoding = self.encoding.split(';')[0]
        if self.verbose:
            print("final encoding is", self.encoding, file=sys.stderr)

        # Is the URL gzipped? If so, we'll need to uncompress it.
        is_gzip = response.info().get('Content-Encoding') == 'gzip'

        # Read the content of the link:
        # This can die with socket.error, "connection reset by peer"
        # And it may not set html, so initialize it first:
        contents = None
        try:
            contents = response.read()
        # XXX Need to guard against IncompleteRead -- but what class owns it??
        #except httplib.IncompleteRead, e:
        #    print("Ignoring IncompleteRead on", url, file=sys.stderr)
        except Exception as e:
            print("Unknown error from response.read()", url, file=sys.stderr)

        # contents can be undefined here.
        # If so, no point in doing anything else.
        if not contents:
            if self.verbose:
                print("Didn't read anything from response.read()",
                      file=sys.stderr)
            response.close()
            raise NoContentError("Empty response.read()")

        if is_gzip:
            buf = io.BytesIO(contents)
            f = gzip.GzipFile(fileobj=buf)
            contents = f.read()

        # No docs say I should close this. I can only assume.
        response.close()

        # response.read() returns bytes. Convert to str as soon as possible
        # so the rest of the program can work with str.
        # But this sometimes fails with:
        # UnicodeDecodeError: 'utf-8' codec can't decode bytes in position nnn-nnn: invalid continuation byte
        try:
            return contents.decode(encoding=self.encoding)
        except UnicodeDecodeError:
            print("UnicodeDecodeError on", self.cur_url)
            return contents.decode(encoding=self.encoding,
                                   errors="backslashreplace")


class FeedmeHTMLParser(FeedmeURLDownloader):

    def __init__(self, feedname):
        super(FeedmeHTMLParser, self).__init__(feedname)

        self.outfile = None
        self.skipping = None
        self.base_href = None

    def fetch_url(self, url, newdir, newname, title=None, author=None,
                  html=None,
                  footer='', referrer=None, user_agent=None):
        """Read a URL from the web. Parse it, rewriting any links,
           downloading any images and making any other changes needed
           according to the config file and current feed name.
           If the optional argument html contains a string,
           skip the downloading and use the html provided.
           Write the modified HTML output to $newdir/$newname,
           (unless newname is None, in which case just return the html)
           and download any images into $newdir.
           Raises NoContentError if it can't get the page or skipped it.
        """
        self.verbose = utils.g_config.getboolean(self.feedname, 'verbose')
        if self.verbose:
            if newname:
                print("Fetching link", url,
                      "to", newdir + "/" + newname, file=sys.stderr)
            else:
                print("Parsing html from", url, "with dir", newdir,
                      file=sys.stderr)

        self.newdir = newdir
        self.newname = newname
        self.cururl = url
        if type(title) is not str:
            title = str(title)
        if type(author) is not str:
            author = str(author)

        # If no base href has been set yet, set it here based on
        # the first URL fetched from RSS.
        if not self.base_href:
            urlparts = urllib.parse.urlparse(url)
            urlparts = urlparts._replace(path='/')
            self.base_href = urllib.parse.urlunparse(urlparts)
            if self.verbose:
                print("On first fetched URL, set base_href to",
                      self.base_href, file=sys.stderr)

        # A flag to indicate when we're skipping everything --
        # e.g. inside <script> tags.
        self.skipping = None

        # Do we need to do any substitution on the URL first?
        urlsub = utils.g_config.get_multiline(self.feedname, 'url_substitute')
        if urlsub:
            if self.verbose:
                print("Multiline: Substituting", urlsub[0],
                      "to", urlsub[1], file=sys.stderr)
                print("Rewriting:", url, file=sys.stderr)
            url = re.sub(urlsub[0], urlsub[1], url)
            if self.verbose:
                print("Became:   ", url, file=sys.stderr)

        self.encoding = utils.g_config.get(self.feedname, 'encoding')
        if not self.encoding:
            self.encoding = "utf-8"

        if not html:
            html = self.download_url(url, referrer, user_agent)

        # Does it contain any of skip_content_pats anywhere? If so, bail.
        skip_content_pats = utils.g_config.get_multiline(self.feedname,
                                                 'skip_content_pats')
        for pat in skip_content_pats:
            if re.search(pat, html):
                raise NoContentError("Skipping, skip_content_pats " + pat)

        if self.newname:
            outfilename = os.path.join(self.newdir, self.newname)
            # XXX Open outfile with the right encoding -- which seems to
            # be a no-op, as we'll still get
            # "UnicodeEncodeError: 'ascii' codec can't encode character
            # unless we explicitly encode everything with fallbacks.
            # So much for python3 being easier to deal with for unicode.
            self.outfile = open(outfilename, "w", encoding=self.encoding)
            self.outfile.write("""<html>\n<head>
<meta http-equiv="Content-Type" content="text/html; charset=%s">
<meta name="viewport" content="width=device-width, initial-scale=1">
<link rel="stylesheet" type="text/css" title="Feeds" href="../../feeds.css"/>
<title>%s</title>
</head>

<body>
""" % (self.encoding, title))
        else:
            outfilename = None
            self.outfile = io.StringIO()

        if author:
            self.outfile.write("By: %s\n<p>\n" % author)

        # Throw out everything before the first page_start re pattern seen,
        # and after the first page_end pattern seen.
        page_starts = utils.g_config.get_multiline(self.feedname, 'page_start')
        page_ends = utils.g_config.get_multiline(self.feedname, 'page_end')

        if len(page_starts) > 0:
            for page_start in page_starts:
                if self.verbose:
                    print("looking for page_start", page_start, file=sys.stderr)
                start_re = re.compile(page_start, flags=re.DOTALL)
                match = start_re.search(html, re.IGNORECASE)
                if match:
                    if self.verbose:
                        print("Found page_start regexp", page_start,
                              file=sys.stderr)
                    html = html[match.end():]
                    break

        if len(page_ends) > 0:
            for page_end in page_ends:
                if self.verbose:
                    print("looking for page_end", page_end, file=sys.stderr)
                end_re = re.compile(page_end, flags=re.DOTALL)
                match = end_re.search(html, re.IGNORECASE)
                if match:
                    if self.verbose:
                        print("Found page_end regexp", page_end,
                              file=sys.stderr)
                    html = html[0:match.start()]
                    break

        # Skip anything matching any of the skip_pats.
        # This is an earlier, regex-based version of skip_nodes.
        # Most sites should use skip_nodes, but there may be some
        # sites where skip_pats works better.
        skip_pats = utils.g_config.get_multiline(self.feedname, 'skip_pats')
        for skip in skip_pats:
            if self.verbose:
                print("Trying to skip '%s'" % skip, file=sys.stderr)
                #print("in", html.encode('utf-8'), file=sys.stderr)
                #sys.stderr.flush()
            # flags=DOTALL doesn't exist in re.sub until 2.7,
            #html = re.sub(skip, '', html, flags=re.DOTALL)
            # but does exist in a compiled re expression:
            try:
                regexp = re.compile(skip, flags=re.DOTALL)
            except Exception as e:
                print("Couldn't compile regexp", skip, file=sys.stderr)
                print(str(e), file=sys.stderr)
                continue
            html = regexp.sub('', html)

        # print("After all skip_pats, html is:", file=sys.stderr)
        # print(html.encode(self.encoding, 'replace'), file=sys.stderr)

        self.single_page_url = None

        # XXX temporarily record the original html src, so we can compare.
        # srcfp = open(outfilename + ".src", "w")
        # srcfp.write(html.encode(self.encoding, 'replace'))
        # srcfp.close()

        # Keep a record of whether we've seen any content:
        self.wrote_data = False

        # Delete any skip_nodes
        skip_nodespecs = utils.g_config.get_multiline(self.feedname,
                                                      'skip_nodes')
        if skip_nodespecs:
            # XXX It's sad to parse with BeautifulSoup and then go back
            # and re-parse the whole document for start and end tags,
            # but with the node begin/end parsing used with lxml,
            # it's hard to delete a node and all its contents.
            # The latter should be rewritten to use BeautifulSoup.
            soup = BeautifulSoup(html, "lxml")

            changed = False
            for nodespec in skip_nodespecs:
                # Syntax is something like: div class="sticky-box"
                # first word should be node type,
                # which may be followed by someattr="somename"
                try:
                    nodename, attrname, attrval = \
                        re.match(SKIP_NODE_PAT, nodespec).groups()
                    for node in soup.find_all(attrs={ attrname: attrval }):
                        node.decompose()
                        changed = True
                except Exception as e:
                    print("Problem finding SKIP_NODE_PAT '%s': %s"
                          % (nodespec, e), file=sys.stderr)
                    utils.ptraceback()
                    continue
            if changed:
                print("Changed nodes in the HTML: rewriting", file=sys.stderr)
                html = str(soup)

        # Does the page have an H1 header already? If not, manufacture one.
        # XXX Would be better to do this check with BeautifulSoup,
        # once that's used for all parsing instead of just skip_nodes.
        if not re.search("<h1", html, re.IGNORECASE):
            self.outfile.write("<h1>%s</h1>\n" % title)

        # Iterate through the HTML, making any necessary simplifications:
        self.feed(html)

        # Did we write anything real, any real content?
        # XXX Currently this requires text, might want to add img tags.
        if not self.wrote_data:
            errstr = "No real content"
            print(errstr, file=sys.stderr)
            self.outfile.close()
            os.remove(outfilename)
            raise NoContentError(errstr)

        # feed() won't write the final tags, so that we can add a footer:
        self.outfile.write(footer)

        self.outfile.write("\n</body>\n</html>\n")

        self.outfile.close()

        # Now we've fetched the normal URL.
        # Did we see a single-page link? If so, move the fetched
        # file aside and call ourselves recursively to try to fetch
        # the single-page.
        if self.single_page_url and self.single_page_url != url:
            # Call ourself recursively.
            # It should only be possible for this to happen once;
            # when we're called recursively, url will be the single
            # page url so we won't make another recursive call.
            singlefile = outfilename + ".single"
            try:
                if self.verbose:
                    print("Trying to fetch single-page url with referrer =", \
                        response.geturl(), "instead of", url, file=sys.stderr)
                self.fetch_url(self.single_page_url, newdir, singlefile,
                               title=title, footer=footer,
                               referrer=response.geturl())

                # If the fetch succeeded and we have a single-page file,
                # replace the original file with it
                # and remove the original.
                if os.path.exists(singlefile):
                    #os.rename(outfilename, outfilename + '.1')
                    os.remove(outfilename)
                    os.rename(singlefile, outfilename)
                    if self.verbose:
                        print("Removing", outfilename, \
                            "and renaming", singlefile, file=sys.stderr)
                else:
                    print("Tried to fetch single-page file "
                          "but apparently failed", file=sys.stderr)
            except (IOError, urllib.error.HTTPError) as e:
                print("Couldn't read single-page URL", \
                    self.single_page_url, file=sys.stderr)
                print(e, file=sys.stderr)

        if not outfilename and type(self.outfile) is io.StringIO():
            return self.outfile.getvalue()

    def feed(self, uhtml):
        """Duplicate, in a half-assed way, HTMLParser.feed() but
           using lxml.html since it handles real-world documents.
           Input is expected to be unicode.
        """
        # Parse the whole document.
        # (Trying valiantly to recover from lxml errors.)
        try:
            tree = lxml.html.fromstring(uhtml)
        except ValueError:
            print("ValueError parsing!")
            # Idiot lxml.html that doesn't give any sensible way
            # to tell what really went wrong:
            if str(sys.exc_info()[1]).startswith(
                "Unicode strings with encoding declaration"):
                # This seems to happen because somehow the html gets
                # something like this inserted at the beginning:
                # <?xml version="1.0" encoding="utf-8"?>
                # So if we've hit the error, try to remove it:
                print("Stupid lxml encoding error on:", file=sys.stderr)
                print(uhtml[:512].encode('utf-8', 'xmlcharrefreplace'),
                      end=' ', file=sys.stderr)
                print('...')

                # Some sample strings that screw up lxml and must be removed:
                # <?xml version="1.0" encoding="ascii" ?>
                uhtml = re.sub('<\?xml .*?encoding=[\'\"].*?[\'\"].*?\?>',
                                '', uhtml)
                tree = lxml.html.fromstring(uhtml)
                print("Tried to remove encoding: now")
                print(uhtml[:512].encode('utf-8',
                                                       'xmlcharrefreplace'), end=' ', file=sys.stderr)
                print('...')
            else:
                raise ValueError

        # Iterate over the DOM tree:
        self.crawl_tree(tree)

        # Eventually we can print it with lxml.html.tostring(tree)

    def crawl_tree(self, tree):
        """For testing:
import lxml.html
html = '<html><body onload="" color="white">\n<p>Hi  ! Ma&ntilde;ana!\n<a href="/my/path/to/link.html">my link</a>\n</body></html>\n'
tree = lxml.html.fromstring(html)
"""
        if type(tree.tag) is str:
            # lxml.html gives comments tag = <built-in function Comment>
            # This is not documented anywhere and there seems to be
            # no way to ask "Is this element a comment?"
            # So we only handle tags that are type str.
            self.handle_starttag(tree.tag, tree.attrib)
            if tree.text:
                self.handle_data(tree.text)
            for node in tree:
                self.crawl_tree(node)
            self.handle_endtag(tree.tag)
        # print the tail even if it was a comment -- the tail is
        # part of the parent tag, not the current tag.
        if tree.tail:
            self.handle_data(tree.tail)

    def handle_starttag(self, tag, attrs):
        #if self.verbose:
        #    print("start tag", tag, attrs)

        # meta refreshes won't work when we're offline, but we
        # might want to display them to give the user the option.
        # <meta http-equiv="Refresh" content="0; URL=http://blah"></meta>
        # meta charset is the other meta tag we care about.
        # All other meta tags will be skipped, so do this test
        # before checking for tag_skippable.
        if tag == 'meta':
            if 'charset' in list(attrs.keys()) and attrs['charset']:
                self.encoding = attrs['charset']
                return
            if 'http-equiv' in list(attrs.keys()) and \
                    attrs['http-equiv'].lower() == 'refresh':
                self.outfile.write("Meta refresh suppressed.<br />")
                if 'content' in list(attrs.keys()):
                    content = attrs['content'].split(';')
                    if len(content) > 1:
                        href = content[1].strip()
                    else:
                        href = content[0].strip()
                    # XXX Next comparison might be better done with re,
                    # in case of spaces around the =.
                    if href.upper().startswith('URL='):
                        href = href[4:]
                    self.outfile.write('<a href="' + href + '">'
                                       + href + '</a>')

                    # Also set the refresh target as the single_page_url.
                    # Maybe we can actually get it here.
                    if not self.single_page_url:
                        self.single_page_url = \
                            imagecache.make_absolute(href, self.base_href)
                        if self.verbose:
                            print("\nTrying meta refresh as single-page pat:", \
                                  self.single_page_url.encode('utf-8',
                                                        'xmlcharrefreplace'),
                                  file=sys.stderr)
                return
                # XXX Note that this won't skip the </meta> tag, unfortunately,
                # and tag_skippable_section can't distinguish between
                # meta refresh and any other meta tags.

        if self.skipping:
            # print("Skipping start tag", tag, "inside a skipped section")
            return

        if tag == 'base' and 'href' in list(attrs.keys()):
            self.base_href = attrs['href']
            return

        # Delete any style tags used for color or things like display:none
        if 'style' in list(attrs.keys()):
            style = attrs['style']
            if re.search('display: *none', style):
                return    # If it's display: none, skip this tag
            # If the style is used to set color or background,
            # keep the tag (it might be there for other reasons)
            # but delete the whole style attribute.
            # XXX Would be nice to make this smarter and delete only
            # color or background.
            if 'color:' in style or 'background:' in style:
                if self.verbose:
                    print("tag", tag, ": deleting style '%s'" % attrs['style'])
                del attrs['style']

        # Some tags, we always skip
        if self.tag_skippable_section(tag):
            self.skipping = tag
            # print("Starting a skippable", tag, "section", file=sys.stderr)
            return

        if self.tag_skippable(tag):
            # print("skipping start", tag, "tag", file=sys.stderr)
            return

        if tag == 'a':
            if 'href' in list(attrs.keys()):
                href = attrs['href']

                # See if this matches the single-page pattern,
                # if we're not already following one:
                if not self.single_page_url:
                    #print("we're not in the single page already")
                    single_page_pats = utils.g_config.get_multiline(self.feedname,
                                                            'single_page_pats')
                    for single_page_pat in single_page_pats:
                        m = re.search(single_page_pat, href)
                        if m:
                            self.single_page_url = \
                                imagecache.make_absolute(
                                    href[m.start():m.end()],
                                    self.base_href)
                            if self.verbose:
                                print("\nFound single-page pattern:", \
                                      self.single_page_url, file=sys.stderr)
                            # But continue fetching the regular pattern,
                            # since the single-page one may fail

                # if self.verbose:
                #     print("Rewriting href", href, end='')
                attrs['href'] = imagecache.make_absolute(href,
                                                         self.base_href)
                # if self.verbose:
                #     print("to", href)

        # Images have so many cases where the image can't be shown.
        if tag != 'img':
            self.write_tag_and_attrs(tag, attrs)
            return

        # If we get here, it's an image, which needs a lot of extra logic.
        # fake_request = urllib.request.Request(self.cur_url)
        # self.host = real_request.host
        tag, attrs = imagecache.process_img_tag(tag, attrs, self.feedname,
                                                self.base_href, self.newdir)

        # Now we've done any needed processing to the img tag and its attrs.
        # It's time to write the start tag to the output file.
        self.write_tag_and_attrs(tag, attrs)

    def write_tag_and_attrs(self, tag, attrs):
        self.outfile.write('<' + tag)

        for attr in list(attrs.keys()):
            # If the tag has style=, arguably we should just remove it entirely.
            # But certainly remove it if it has style="font-anything" --
            # don't want to let the page force its silly ideas of font
            # size or face.
            # And yes, this means we'll lose any other styles that are
            # specified along with a font style. Probably no loss!
            if attr == 'style' and 'font' in attrs[attr]:
                continue

            self.outfile.write(' ' + attr)
            if attrs[attr] and type(attrs[attr]) is str:
                # make sure attr[1] doesn't have any embedded double-quotes
                val = attrs[attr].replace('"', '\"')
                self.outfile.write('="' + val + '"')

        self.outfile.write('>')

    def handle_endtag(self, tag):
        if tag == self.skipping:
            self.skipping = False
            # print("Ending a skippable", tag, "section", file=sys.stderr)
            return
        if self.skipping:
            # print("Skipping end tag", tag, "inside a skipped section")
            return
        if self.tag_skippable(tag) or self.tag_skippable_section(tag):
            # print("Skipping end", tag, file=sys.stderr)
            return

        # Some tags don't have ends, and it can cause problems:
        # e.g. <br></br> displays as two breaks, not one.
        if tag in [ "br", "img" ]:
            return

        # Don't close the body or html -- caller may want to add a footer.
        if tag == "body" or tag == 'html':
            return

        self.outfile.write('</' + tag + '>\n')

    def handle_data(self, data):
        # XXX lxml.etree.tostring() might be a cleaner way of printing
        # these nodes: http://lxml.de/tutorial.html
        if self.skipping:
            return

        # If it's not just whitespace, make a note that we've written something.
        if data.strip():
            self.wrote_data = True

        if type(data) is str:
            # XXX This is getting:
            # UnicodeEncodeError: 'ascii' codec can't encode character '\u2019' in position 193: ordinal not in range(128)
            # How do we protect against that?
            # Is there any reliable way to write str to a file in python3?
            self.outfile.write(data)
        elif self.verbose:
            print("Data isn't str! type =", type(data), file=sys.stderr)

    # def handle_entityref(self, name):
    #     if self.skipping:
    #         #print("Skipping entityref")
    #         return
    #     self.outfile.write('&' + name + ';')

    def tag_skippable_section(self, tag):
        """Skip certain types of tags we don't want in simplified HTML.
           This skips everything until the matching end tag.
        """
        # script and style tags aren't helpful in minimal offline reading
        if tag == 'script' or tag == 'style':
            return True

        # base tags can confuse the HTML displayer program
        # into looking remotely for images we've copied locally.
        if tag == 'base':
            return True

        # Omit generic objects, which are probably flash or video or some such.
        if tag == 'object':
            return True

        # Omit form elements, since it's too easy to land on them accidentally
        # when scrolling and trigger an unwanted Android onscreen keyboard:
        if tag == 'input' or tag == 'textarea':
            return True

        # Omit iframes -- they badly confuse Android's WebView
        # (goBack fails if there's an iframe anywhere in the page:
        # you have to goBack multiple times, I think once for every
        # iframe in the page, and this doesn't seem to be a bug
        # that's getting fixed any time soon).
        # We don't want iframes in simplified HTML anyway.
        if tag == 'iframe':
            return True

        # Don't want embedded <head> stuff
        # Unfortunately, skipping the <head> means we miss meta and base.
        # Missing meta is a problem because it means we don't get the charset.
        # XXX But note: we probably won't see the charset anyway, because
        # we'll look for it in the first head, the one we create ourselves,
        # rather than the one that comes from the original page.
        # We really need to merge the minimal information from the page
        # head into the generated one.
        # Meanwhile, these tags may do more harm than good.
        # We definitely need to remove <link type="text/css".
        if tag == 'head':
            return True

        return False

    def tag_skippable(self, tag):
        """Skip certain types of tags we don't want in simplified HTML.
           This will not remove tags inside the skipped tag, only the
           skipped tag itself.
        """
        if tag == 'form':
            return True

        # The source tag is used to specify alternate forms of media.
        # But the LA Daily Post uses it for images, and many browsers
        # including Android WebView use it to override the img src attribute.
        # So leaving in the source tag may cause images to be fetched
        # from the net rather than from the locally fetched files.
        if tag == 'source':
            return True

        # If we're skipping images, we could either omit them
        # entirely, or leave them in with their src= unchanged
        # so that a network-connected viewer can still fetch them.
        # For now, let's opt to remove them.
        if (utils.g_config.getboolean(self.feedname, 'skip_images') and
            (tag == 'img' or tag == 'svg' or tag == "figure")):
            return True

        # Skip videos regardless of the skip_images setting,
        # since there's no mechanism to download videos,
        # and including them inline leads to unwanted data charges.
        # Some day maybe this could be a separate pref.
        if tag == 'video':
            return True

        if tag == 'a' and \
                utils.g_config.getboolean(self.feedname, 'skip_links'):
            return True

        # Embedded <body> tags often have unfortunate color settings.
        # Embedded <html> tags don't seem to do any harm, but seem wrong.
        if tag == 'body' or tag == 'html' or tag == 'meta':
            return True

        # Font tags are almost always to impose colors that don't
        # work against an arbitrary background.
        if tag == 'font':
            return True

        # <link rel="stylesheet" isn't always in the head.
        # Undark puts them at the end of the document but they still
        # apply to the whole document, making text unreadable.
        # I don't know of any other legitimate uses for <link>
        # so let's just remove them all.
        if tag == 'link':
            return True

        return False

class HTMLSimplifier:
    keeptags = [ 'p', 'br', 'div' ]
    encoding = 'utf-8'

    def __init__(self):
        self.outstr = ''

    def simplify(self, htmlstring):
        tree = lxml.html.fromstring(htmlstring)
        self.crawl_tree(tree)
        return self.outstr

    def crawl_tree(self, tree):
        if type(tree.tag) is str:
            # lxml.html gives comments tag = <built-in function Comment>
            # This is not documented anywhere and there seems to be
            # no way to ask "Is this element a comment?"
            # So we only handle tags that are type str.
            self.handle_starttag(tree.tag, tree.attrib)
            if tree.text:
                #print(tree.tag, "contains text", tree.text)
                self.handle_data(tree.text)
            for node in tree:
                self.crawl_tree(node)
            self.handle_endtag(tree.tag, tree.attrib)
        # print the tail even if it was a comment -- the tail is
        # part of the parent tag, not the current tag.
        if tree.tail:
            #print(tree.tag, "contains text", tree.tail)
            self.handle_data(tree.tail)

    def handle_starttag(self, tag, attrs):
        # Only keep a few well-defined tags:
        if tag in self.keeptags:
            self.outstr += "<%s>" % tag

    def handle_endtag(self, tag, attrs):
        # Only keep a few well-defined tags:
        if tag in self.keeptags:
            self.outstr += "</%s>" % tag

    def handle_data(self, data):
        if type(data) is str:
            self.outstr += data
        else:
            print("Data isn't str! type =", type(data), file=sys.stderr)


#
# Adapted from:
# https://stackoverflow.com/a/33078599
#  Author: Noah Fontes nfontes AT cynigram DOT com
#  License: MIT
#  Original:
#    http://blog.mithis.net/archives/python/90-firefox3-cookies-in-python
#  Ported to Python 3 by Dotan Cohen
#
def get_firefox_cookie_jar(filename):
    """
    Create a CookieJar based on a Firefox cookies.sqlite.
    """

    import sqlite3
    from http.cookiejar import MozillaCookieJar

    con = sqlite3.connect(filename)
    cur = con.cursor()
    cur.execute("SELECT host, path, isSecure, expiry, name, value "
                "FROM moz_cookies")

    ftstr = ["FALSE", "TRUE"]

    s = io.StringIO()
    s.write("""\
# Netscape HTTP Cookie File
# http://www.netscape.com/newsref/std/cookie_spec.html
# This is a generated file!  Do not edit.
""")

    for item in cur.fetchall():
        s.write("%s\t%s\t%s\t%s\t%s\t%s\t%s\n" % (
            item[0], ftstr[item[0].startswith('.')], item[1],
            ftstr[item[2]], item[3], item[4], item[5]))

    s.seek(0)
    cookie_jar = MozillaCookieJar()
    cookie_jar._really_load(s, '', True, True)

    return cookie_jar


