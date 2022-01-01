#!/usr/bin/env python3

# URL parser for feedme, http://shallowsky.com/software/feedme/
# Copyright 2011-2017 by Akkana Peck.
# Share and enjoy under the GPL v2 or later.

from __future__ import print_function

import os, sys
import urllib.request, urllib.error, urllib.parse
import re
from configparser import ConfigParser
import lxml.html
from http.cookiejar import CookieJar
import io
import gzip
import traceback

# We'll use XDG for the config and cache directories if it's available
try:
    import xdg.BaseDirectory
except:
    pass


VersionString = "FeedMe 1.1b1"

has_ununicode=True

# Python3 seems to have no straightforward way to just print a
# simple traceback without going into several levels of recursive
# "During handling of the above exception, another exception occurred"
# if there's anything involved that might have a nonascii character.
# This doesn't work reliably either:
# TypeError: unorderable types: int() < traceback() in the print line.
# or, more recently,
# '>=' not supported between instances of 'traceback' and 'int'
def ptraceback():
    try:
        # This tends to raise an exception,
        #    traceback unorderable types: traceback() >= int()
        # for no reason anyone seems to know:
        # ex_type, ex, tb = sys.exc_info()
        # print(str(traceback.format_exc(tb)), file=sys.stderr)
        # so instead:

        print("\n====== Stack trace was:", file=sys.stderr)
        traceback.print_exc(file=sys.stderr)
        print("====== end stack trace\n", file=sys.stderr)
    except Exception as e:
        print("******** Yikes! Exception trying to print traceback:", e,
              file=sys.stderr)


# XXX
# This doesn't work any more, in the Python 3 world, because everything
# is already encoded into a unicode string before we can get here.
# If I ever need to go back and support ununicode or re-coding,
# I'll have to revisit this.

# try:
#     import ununicode
# except ImportError as e:
#     has_ununicode=False
#
# def output_encode(s, encoding):
#     if encoding == 'ascii' and has_ununicode:
#         #return unicodedata.normalize('NFKD', s).encode('ascii', 'ignore')
#         # valid values in encode are replace and ignore
#         return ununicode.toascii(s,
#                                  in_encoding=encoding,
#                                  errfilename=os.path.join(outdir,
#                                                           "errors"))
#     elif isinstance(s, str):
#         return s.encode('utf-8', 'backslashreplace')
#     else:
#         return s


def get_config_multiline(config, feedname, configname):
    configlines = config.get(feedname, configname)
    if configlines != '':
        configlines = configlines.split('\n')
    else:
        configlines = []
    return configlines


class NoContentError(Exception):
    pass


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

    def __init__(self, config, feedname):
        self.config = config
        self.feedname = feedname
        self.user_agent = VersionString
        self.encoding = None
        self.cookiejar = None

    def download_url(self, url, referrer=None, user_agent=None, verbose=False):
        """Download a URL (likely http or RSS) from the web and return its
           contents as a str. Allow for possible vagaries like cookies,
           redirection, compression etc.
        """
        if not user_agent:
            user_agent = VersionString
        if verbose:
            print("download_url", url, "referrer=", referrer, \
                                "user_agent", user_agent, file=sys.stderr)

        request = urllib.request.Request(url)

        # If we're after the single-page URL, we may need a referrer
        if referrer:
            if verbose:
                print("Adding referrer", referrer, file=sys.stderr)
            request.add_header('Referer', referrer)

        request.add_header('User-Agent', user_agent)
        if verbose:
            print("Using User-Agent of", user_agent, file=sys.stderr)

        if not self.cookiejar:
            # Create the cookiejar once per site; it will be reused
            # for all site stories fetched, but it won't be saved
            # for subsequent days.
            self.cookiejar = None
            cookiefile = self.config.get(self.feedname, "cookiefile",
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
        response = opener.open(request, timeout=100)
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
        if self.config.getboolean(self.feedname, 'allow_gzip'):
            request.add_header('Accept-encoding', 'gzip')

        # feed() is going to need to know the host, to rewrite urls.
        # So save host and prefix based on any redirects we've had:
        # feedmeparser will need them.
        self.host = real_request.host
        self.prefix = real_request.type + '://' + self.host + '/'

        # urllib2 unfortunately doesn't read unicode,
        # so try to figure out the current encoding:
        if not self.encoding:
            if verbose:
                print("download_url: self.encoding not set, getting it from headers", file=sys.stderr)
            self.encoding = response.headers.get_content_charset()
            enctype = response.headers['content-type'].split('charset=')
            if len(enctype) > 1:
                self.encoding = enctype[-1]
            else:
                if verbose:
                    print("Defaulting to utf-8", file=sys.stderr)
                self.encoding = 'utf-8'
        if verbose:
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
            print("Didn't read anything from response.read()", file=sys.stderr)
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

    def __init__(self, config, feedname):
        super(FeedmeHTMLParser, self).__init__(config, feedname)

        self.outfile = None
        self.skipping = None
        self.remapped_images = {}
        self.base_href = None
        self.verbose = False

    def fetch_url(self, url, newdir, newname, title=None, author=None,
                  html=None,
                  footer='', referrer=None, user_agent=None):
        """Read a URL from the web. Parse it, rewriting any links,
           downloading any images and making any other changes needed
           according to the config file and current feed name.
           If the optional argument html contains a string,
           skip the downloading and use the html provided.
           Write the modified HTML output to $newdir/$newname,
           and download any images into $newdir.
           Raises NoContentError if it can't get the page or skipped it.
        """
        self.verbose = self.config.getboolean(self.feedname, 'verbose')
        if self.verbose:
            print("Fetching link", url, \
                "to", newdir + "/" + newname, file=sys.stderr)

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
            print("On first fetched URL, set base_href to",
                  self.base_href, file=sys.stderr)

        # A flag to indicate when we're skipping everything --
        # e.g. inside <script> tags.
        self.skipping = None

        # Do we need to do any substitution on the URL first?
        urlsub = get_config_multiline(self.config, self.feedname,
                                      'url_substitute')
        if urlsub:
            print("Substituting", urlsub[0], "to", urlsub[1], file=sys.stderr)
            print("Rewriting:", url, file=sys.stderr)
            url = re.sub(urlsub[0], urlsub[1], url)
            print("Became:   ", url, file=sys.stderr)

        self.encoding = self.config.get(self.feedname, 'encoding')
        if not self.encoding:
            self.encoding = "utf-8"

        if not html:
            html = self.download_url(url, referrer, user_agent,
                                     verbose=self.verbose)

        # Does it contain any of skip_content_pats anywhere? If so, bail.
        skip_content_pats = get_config_multiline(self.config, self.feedname,
                                                 'skip_content_pats')
        for pat in skip_content_pats:
            if re.search(pat, html):
                raise NoContentError("Skipping, skip_content_pats " + pat)

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

        if author:
            self.outfile.write("By: %s\n<p>\n" % author)

        # Throw out everything before the first page_start re pattern seen,
        # and after the first page_end pattern seen.
        page_starts = get_config_multiline(self.config, self.feedname,
                                           'page_start')
        page_ends = get_config_multiline(self.config, self.feedname, 'page_end')

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
        # It may eventually be better to do this in the HTML parser.
        skip_pats = get_config_multiline(self.config, self.feedname,
                                         'skip_pats')
        if len(skip_pats) > 0:
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

        # Does the page have an H1 header already? If not,
        # we can manufacture one.
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
                    print("Tried to fetch single-page file but apparently failed", file=sys.stderr)
            except (IOError, urllib.error.HTTPError) as e:
                print("Couldn't read single-page URL", \
                    self.single_page_url, file=sys.stderr)
                print(e, file=sys.stderr)

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
            print("ValueError!")
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

    def rewrite_images(self, content, encoding=None):
        """Rewrite img src tags to point to local images we downloaded earlier.
           We already rewrote the img tags in the HTML file, but feedme
           may need us to rewrite img tags embedded in the RSS content.
        """
        try:
            # And yes, BeautifulSoup would be more straightforward here.
            # But we're already using lxml.html for the rest of the parsing.
            # XXX TODO rewrite in BeautifulSoup.
            tree = lxml.html.fromstring(content)
            for e in tree.iter():
                if e.tag == 'img':
                    if 'src' in list(e.keys()):
                        try:
                            src = self.make_absolute(e.attrib['src'])
                            if src in list(self.remapped_images.keys()):
                                e.attrib['src'] = self.remapped_images[src]
                                continue
                        except KeyError:
                            print("KeyError remapping img src",
                                  e.attrib['src'],
                                  file=sys.stderr)
                            pass
                        if self.verbose:
                            print("Removing img", e.attrib['src'],
                                  file=sys.stderr)
                        e.drop_tree()
                    # img src wasn't in e.keys, or remapping it
                    # didn't result in the right attribute.
                    else:
                        if self.verbose:
                            print("Removing img with no src", file=sys.stderr)
                        e.drop_tree()

            # lxml.html.tostring returns bytes, despite the name.
            # And converting it with str() doesn't work,
            # must use decode with a charset.
            if not encoding:
                # We may or may not have a self.encoding defined here;
                # for the indexstr we often don't, so default to UTF-8.
                if not self.encoding:
                    self.encoding = "UTF-8"
                encoding = self.encoding
            return lxml.html.tostring(tree).decode(encoding=encoding)

        except Exception as e:
            print("Couldn't rewrite images in content:" + str(e),
                  file=sys.stderr)
            print("Content type:", type(content), file=sys.stderr)
            ptraceback()
            return content

    # The srcset spec is here:
    # http://w3c.github.io/html/semantics-embedded-content.html#element-attrdef-img-srcset
    # https://html.spec.whatwg.org/multipage/images.html#srcset-attribute
    # A simple version is easy:
    # srcset="http://site/img1.jpg 1024w,
    #         http://site/img1.jpg 150w, ..."
    # but some sites, like Wired, embed commas inside their
    # image URLs. Since the spaces aren't required, that
    # makes the w and the comma the only way to parse it,
    # and w and comma are both legal URL characters.
    #
    # The key is: "If an image candidate string contains no descriptors
    # and no ASCII whitespace after the URL, the following image candidate
    # string, if there is one, must begin with one or more ASCII whitespace."
    # so basically, a comma that's separating image descriptors
    # either has to have a space after it, or a w or x before it.
    def parse_srcset(self, srcset_attr):
        '''Parse a SRCSET attribute inside an IMG tag.
           Return a list of pairs [(img_url, descriptor), ...]
           where the descriptor is a resolution ending in w (pixel width)
           or x (pixel density).
        '''
        commaparts = srcset_attr.split(',')
        parts = []
        for part in commaparts:
            # First, might we be continuing an image URL from the previous part?
            # That's the case if the previous part lacked a descriptor
            # and this part doesn't start with a space.
            if not part.startswith(' ') and parts and not parts[-1][1]:
                part = parts.pop(-1)[0] + ',' + part

            # Does this part have a descriptor?
            match = re.search(' *[0-9]+[wx]$', part)
            if match:
                matched = match.group()
                parts.append((part[0:-len(matched)].strip(), matched.strip()))
            else:
                # Possibly shouldn't strip this, in case there's a
                # continuation (this might only be the first part of
                # an image URL, before a comma) and the URL might have
                # a space in it. But since spaces in URLs are illegal,
                # let's hope for now that no one does that.
                parts.append((part.strip(), None))

        return parts

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
                            self.make_absolute(href)
                        print("\nTrying meta refresh as single-page pattern:", \
                            self.single_page_url.encode('utf-8',
                                                        'xmlcharrefreplace'), file=sys.stderr)
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
                return    # Yes, discard the whole style tag
            if re.search('color:', style):
                return
            if re.search('background', style):
                return

        # Some tags, we always skip
        if self.tag_skippable_section(tag):
            self.skipping = tag
            # print("Starting a skippable", tag, "section", file=sys.stderr)
            return

        if self.tag_skippable(tag):
            # print("skipping start", tag, "tag", file=sys.stderr)
            return

        #print("type(tag) =", type(tag))
        self.outfile.write('<' + tag)

        if tag == 'a':
            if 'href' in list(attrs.keys()):
                href = attrs['href']

                # See if this matches the single-page pattern,
                # if we're not already following one:
                if not self.single_page_url:
                    #print("we're not in the single page already")
                    single_page_pats = get_config_multiline(self.config,
                                                            self.feedname,
                                                            'single_page_pats')
                    for single_page_pat in single_page_pats:
                        m = re.search(single_page_pat, href)
                        if m:
                            self.single_page_url = \
                                self.make_absolute(href[m.start():m.end()])
                            print("\nFound single-page pattern:", \
                                  self.single_page_url, file=sys.stderr)
                            # But continue fetching the regular pattern,
                            # since the single-page one may fail

                # print("Rewriting href", href, "to", self.make_absolute(href))
                attrs['href'] = self.make_absolute(href)

        elif tag == 'img':
            keys = list(attrs.keys())
            # Handle both src and srcset.
            if 'src' in keys:
                src = attrs['src']
            else:
                src = None
            if 'srcset' in keys or 'data-lazy-srcset' in keys:
                # The intent here:
                # If there's a srcset, pick the largest one that's still
                # under max_srcset_size, and set src to that.
                # That's what we'll try to download.
                # Then remove the srcset attribute.
                try:
                    maximgwidth = int(self.config.get(self.feedname,
                                                      'max_srcset_size'))
                except:
                    maximgwidth = 800

                # ladailypost has a crazy setup
                # where they set the src to something that isn't an image,
                # then have data-lazy-src and/or data-lazy-srcset
                # which presumably get loaded later with JavaScript.
                if 'data-lazy-srcset' in attrs:
                    srcset = self.parse_srcset(attrs['data-lazy-srcset'])
                    print("parsed lazy srcset, srcset is", srcset)
                elif 'srcset' in attrs:
                    srcset = self.parse_srcset(attrs['srcset'])
                else:
                    srcset = None

                if srcset:
                    try:
                        curimg = None
                        curwidth = 0
                        for pair in srcset:
                            w = pair[1].strip().lower()
                            if not w.endswith('w'):
                                # It probably ends in x and is a resolution-based
                                # descriptor. We don't handle those yet,
                                # but just in case we don't see any width-based
                                # ones, let's save the image.
                                if not curimg:
                                    curimg = pair[0].strip()
                                print("srcset non-width descriptor '%s" % w,
                                      file=sys.stderr)
                                continue
                            w = int(w[:-1])
                            if w > curwidth and w <= maximgwidth:
                                curwidth = w
                                curimg = pair[0].strip()
                                print("Using '%s' at width %d" % (curimg, curwidth),
                                      file=sys.stderr)
                        if curimg:
                            src = curimg
                    except:
                        # Wired sometimes has srcset with just a single url
                        # that's the same as the src=. In that case it
                        # wouldn't do us any good anyway.
                        # And there are all sorts of nutty and random things
                        # sites do with srcset.
                        print("Error parsing srcset: %s" % attrs['srcset'],
                              file=sys.stderr)
                        pass

            if not src:
                # Don't do anything to this image, it has no src or srcset
                return

            if 'srcset' in keys:
                del attrs['srcset']

            # Make relative URLs absolute
            src = self.make_absolute(src)
            if not src:
                return
            if src.startswith("data:"):
                # With a data: url we already have all we need
                return

            # urllib2 can't parse out the host part without first
            # creating a Request object.
            # Quote it to guard against URLs with nonascii characters,
            # which will make urllib.request.urlopen bomb out with
            # UnicodeEncodeError: 'ascii' codec can't encode character.
            # If this proves problematical, try the more complicated
            # solution at https://stackoverflow.com/a/40654295
            req = urllib.request.Request(urllib.parse.quote(src, safe=':/'))
            req.add_header('User-Agent', self.user_agent)

            # Should we only fetch images that come from the HTML's host?
            try:
                nonlocal_images = self.config.getboolean(self.feedname,
                                                         'nonlocal_images')
            except:
                nonlocal_images = False

            # Should we rewrite images that come from elsewhere,
            # to avoid unwanted data use?
            try:
                block_nonlocal = self.config.getboolean(self.feedname,
                                                        'block_nonlocal_images')
            except:
                block_nonlocal = False

            # If we can't or won't download an image, what should
            # we replace it with?
            if block_nonlocal:
                print("Using bogus image source for nonlocal images",
                      file=sys.stderr)
                alt_src = 'file:///nonexistant'
                # XXX Would be nice, in this case, to put a link around
                # the image so the user could tap on it if they wanted
                # to see it, at least if it isn't already inside a link.
                # That would be easy in BeautifulSoup but it's hard
                # with this start/end tag model.
            else:
                alt_src = src

            alt_domains = get_config_multiline(self.config, self.feedname,
                                               'alt_domains')
            if nonlocal_images or self.similar_host(req.host, self.host,
                                                    alt_domains):
                # base = os.path.basename(src)
                # For now, don't take the basename; we want to know
                # if images are unique, and the basename alone
                # can't tell us that.
                base = src.replace('/', '_')
                # Clean up the filename, since it might have illegal chars.
                # Only allow alphanumerics or others in a short whitelist.
                # Don't allow % in the whitelist -- it causes problems
                # with recursively copying the files over http later.
                base = ''.join([x for x in base if x.isalpha() or x.isdigit()
                                or x in '-_.'])
                if not base : base = '_unknown.img'
                imgfilename = os.path.join(self.newdir, base)

                # Some sites, like High Country News, use the same image
                # name for everything (e.g. they'll have
                # storyname-0418-jpg/image, storyname-0418-jpg/image etc.)
                # so we can't assume that just because the basename is unique,
                # the image must be.
                # if os.path.exists(imgfilename) and \
                #    src not in self.remapped_images:
                #     howmany = 2
                #     while True:
                #         newimgfile = "%d-%s" % (howmany, imgfilename)
                #         if not os.path.exists(newimgfile):
                #             imgfilename = newimgfile
                #             break
                #         howmany += 1
                # But we don't need this clause if we use the whole image path,
                # not just the basename.

                # Check again for a data: URL, in case it came in
                # from one of the src substitutions.
                if src.startswith("data:"):
                    return

                try:
                    if not os.path.exists(imgfilename):
                        print("Fetching image", src, "to", imgfilename,
                              file=sys.stderr)
                        # urllib.request.urlopen is supposed to have
                        # a default timeout, but if so, it must be
                        # many minutes. Try this instead.
                        f = urllib.request.urlopen(req, timeout=100)
                        # Lots of things can go wrong with downloading
                        # the image, such as exceptions.IOError from
                        # [Errno 36] File name too long
                        # XXX Might want to wrap this in its own try.
                        local_file = open(imgfilename, "wb")
                        # Write to our local file
                        local_file.write(f.read())
                        local_file.close()
                    #else:
                    #    print("Not downloading, already have", imgfilename)

                    # If we got this far, then we have a local image,
                    # so go ahead and rewrite the url:
                    self.remapped_images[src] = base
                    attrs['src'] = base

                # handle download errors
                except urllib.error.HTTPError as e:
                    print("HTTP Error on image:", e.code,
                          "on", src, ": setting img src to", alt_src,
                          file=sys.stderr)
                    # Since we couldn't download, point instead to the
                    # absolute URL, so it will at least work with a
                    # live net connection.
                    attrs['src'] = alt_src
                except urllib.error.URLError as e:
                    print("URL Error on image:", e.reason,
                          "on", src, file=sys.stderr)
                    attrs['src'] = alt_src
                except Exception as e:
                    print("Error downloading image:", str(e), \
                        "on", src, file=sys.stderr)
                    ptraceback()
                    attrs['src'] = alt_src
            else:
                # Looks like it's probably a nonlocal image.
                print(req.host, "and", self.host,
                      "are too different -- not fetching image", src,
                      file=sys.stderr)
                # But that means we're left with a nonlocal image in the source.
                # That could mean unwanted data use to fetch the image
                # when viewing the file. So remove the image tag and
                # replace it with a link.
                attrs['src'] = alt_src

        # Now we've done any needed processing to the tag and its attrs.
        # It's time to write the start tag to the output file.
        for attr in list(attrs.keys()):
            # If the tag has style=, arguably we should just remove it entirely.
            # But certainly remove it if it has style="font-anything" --
            # don't want to let the page force its silly ideas of font
            # size or face.
            # And yes, this means we'll lose any other styles that are
            # specified along with a font style. Probably no loss!
            if attr == 'style' and 'font' in attrs[attr]:
                print("Skipping a style tag!", attrs[attr], file=sys.stderr)
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
        else:
            print("Data isn't str! type =", type(data), file=sys.stderr)

    # def handle_entityref(self, name):
    #     if self.skipping:
    #         #print("Skipping entityref")
    #         return
    #     self.outfile.write('&' + name + ';')

    def similar_host(self, host1, host2, alt_domains):
        """Are two hosts close enough for the purpose of downloading images?
           Or is host1 close to anything in alt_domains?
        """
        if self.same_host(host1, host2):
            return True
        for d in alt_domains:
            if self.same_host(host1, d):
                return True
        return False

    def same_host(self, host1, host2):
        """Are two hosts close enough for the purpose of downloading images?"""

        # host can be None:
        if not host1 and not host2:
            return True
        if not host1 or not host2:
            return False

        # For now, a simplistic comparison:
        # are the last two elements (foo.com) the same?
        # Eventually we might want smarter special cases,
        # exceptions for akamai, etc.
        return host1.split('.')[-2:] == host2.split('.')[-2:]

    def make_absolute(self, url):
        '''Make URLs, particularly img src, absolute according to
           the current page location and any base href we've seen.
        '''
        # May want to switch to lxml.html.make_links_absolute(base_href,
        # resolve_base_href=True)

        if not url:
            return url

        if '://' in url:
            return url       # already absolute

        # If we have a base href then it doesn't matter whether it's
        # relative or absolute.
        if self.base_href:
            return urllib.parse.urljoin(self.base_href, url)

        # Map paths without a schema or host to the full URL.
        # Set it here from from the site RSS UR, though that isn't
        # always right, e.g. https://rss.example.com.
        # XXX We should always have a base_href here since it should
        # have been set the first time through fetch_url,
        # so this clause should never trigger. But just in case,
        # leave this clause here for a while.
        if url[0] == '/':
            if not self.base_href:
                print("******** Yikes, got to make_absolute with no base_url",
                      file=sys.stderr)
                url = self.config.get(self.feedname, 'url')
                urlparts = urllib.parse.urlparse(url)
                urlparts = urlparts._replace(path='/')
                self.base_href = urllib.parse.urlunparse(urlparts)
                print("Set base_href to", self.base_href, file=sys.stderr)

            return urllib.parse.urljoin(self.base_href, url)

        # It's relative, so append it to the current url minus cur filename:
        return os.path.join(os.path.dirname(self.cururl), url)

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
        if (tag == 'img' or tag == 'svg' or tag == 'video') and \
                self.config.getboolean(self.feedname, 'skip_images'):
            return True

        if tag == 'a' and \
                self.config.getboolean(self.feedname, 'skip_links'):
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

#
# Keep track of the config file directory
#
default_confdir = None
def init_default_confdir():
    global default_confdir
    if 'XDG_CONFIG_HOME' in os.environ:
        confighome = os.environ['XDG_CONFIG_HOME']
    elif 'xdg.BaseDirectory' in sys.modules:
        confighome = xdg.BaseDirectory.xdg_config_home
    else:
        confighome = os.path.join(os.environ['HOME'], '.config')

    default_confdir = os.path.join(confighome, 'feedme')

init_default_confdir()
print("default_confdir:", default_confdir)

#
# Read the configuration files
#
def read_config_file(confdir=None):
    '''Read the config file from XDG_CONFIG_HOME/feedme/*.conf,
       returning a ConfigParser object'''

    if not confdir:
        confdir = default_confdir

    main_conf_file = 'feedme.conf'
    conffile = os.path.join(confdir, main_conf_file)
    if not os.access(conffile, os.R_OK):
        print("Error: no config file in", conffile, file=sys.stderr)
        sys.exit(1)

    config = ConfigParser({'url' : '',
                           'verbose' : 'false',
                           'levels' : '2',
                           'encoding' : '',  # blank means try several
                           'page_start' : '',
                           'page_end':'',
                           'single_page_pats' : '',
                           'url_substitute' : '',
                           'simplify_rss' : 'false',
                           'rss_entry_size' : '0',  # max size in bytes

                           # Patterns to skip within a story.
                           # Anything within the regexps will be excised
                           # from the story.
                           'skip_pats' : '',

                           # Various triggers for skipping a whole story:
                           # Skip links with these patterns:
                           'skip_link_pats' : '',
                           # Skip anything with titles containing these:
                           'skip_title_pats' : '',
                           # Skip anything whose content includes these:
                           'skip_content_pats' : '',
                           # Skip anything where the index content includes:
                           'index_skip_content_pats' : '',

                           # acceptable alternate sources for images:
                           'alt_domains' : '',

                           # module for special URL downloading:
                           'page_helper' : '',
                           # Single string argument passed to the helper.
                           'helper_arg' : '',

                           'nocache' : 'false',
                           'allow_repeats': 'false',
                           'logfile' : '',
                           'save_days' : '7',
                           'skip_images' : 'true',
                           'nonlocal_images' : 'false',
                           'block_nonlocal_images' : 'false',
                           'skip_links' : 'false',
                           'when' : '',  # Day, like tue, or month-day, like 14
                           'min_width' : '25', # min # chars in an item link
                           'continue_on_timeout' : 'false',
                           'user_agent' : VersionString,
                           'ascii' : 'false',
                           'allow_gzip' : 'true'})

    config.read(conffile)
    for fil in os.listdir(confdir):
        if fil.endswith('.conf') and fil != main_conf_file:
            filepath = os.path.join(confdir, fil)
            if os.access(filepath, os.R_OK):
                config.read(filepath)
            else:
                print("Can't read", filepath)

    return config

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
    from io import StringIO
    from http.cookiejar import MozillaCookieJar

    con = sqlite3.connect(filename)
    cur = con.cursor()
    cur.execute("SELECT host, path, isSecure, expiry, name, value "
                "FROM moz_cookies")

    ftstr = ["FALSE", "TRUE"]

    s = StringIO()
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


if __name__ == '__main__':
    config = read_config_file()

    parser = FeedmeHTMLParser(config, 'Freaktest')
    parser.fetch_url('http://www.freakonomics.com/2011/12/21/what-to-do-with-cheating-students/', '/home/akkana/feeds/Freaktest/', 'test.html', "Freak Test")

