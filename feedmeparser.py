#!/usr/bin/env python3

# URL parser for feedme, http://shallowsky.com/software/feedme/
# Copyright 2011-2017 by Akkana Peck.
# Share and enjoy under the GPL v2 or later.

from __future__ import print_function

import os, sys
import urllib.request, urllib.error, urllib.parse
import re
from configparser import ConfigParser
#from HTMLParser import HTMLParser
import lxml.html
import urllib.parse
from http.cookiejar import CookieJar
import io
import gzip
import traceback

has_ununicode=True

# XXX
# This doesn't work any more, in the Python 3 world, because everything
# is already encoded into a unicode string before we can get here.
# If we ever need to go back and support ununicode or re-coding,
# We'll have to revisit this.

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

VersionString = "FeedMe 1.0b3"

def get_config_multiline(config, feedname, configname):
    configlines = config.get(feedname, configname)
    if configlines != '':
        configlines = configlines.split('\n')
    else:
        configlines = []
    return configlines

class NoContentError(Exception):
    pass

class FeedmeURLDownloader(object):

    def __init__(self, config, feedname):
        self.config = config
        self.feedname = feedname
        self.user_agent = VersionString
        self.encoding = None

    def download_url(self, url, referrer=None, user_agent=None, verbose=False):
        """Download a URL (likely http or RSS) from the web and return its
           contents as a str. Allow for possible vagaries like cookies,
           redirection, compression etc.
        """
        if verbose:
            print("download_url", url, "referrer=", referrer, \
                                "user_agent", user_agent, file=sys.stderr)

        request = urllib.request.Request(url)

        # If we're after the single-page URL, we may need a referrer
        if referrer:
            if verbose:
                print("Adding referrer", referrer, file=sys.stderr)
            request.add_header('Referer', referrer)

        if not user_agent:
            user_agent = VersionString
        request.add_header('User-Agent', user_agent)
        if verbose:
            print("Using User-Agent of", user_agent, file=sys.stderr)

        # Allow for cookies in the request: some sites, notably nytimes.com,
        # degrade to an infinite redirect loop if cookies aren't enabled.
        cj = CookieJar()
        opener = urllib.request.build_opener(urllib.request.HTTPCookieProcessor(cj))
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
        #    print >>sys.stderr, "Ignoring IncompleteRead on", url
        except Exception as e:
            print("Unknown error from response.read()", url, file=sys.stderr)

        # contents can be undefined here. If so, no point in doing anything else.
        if not contents:
            print("Didn't read anything from response.read()", file=sys.stderr)
            response.close()
            raise NoContentError

        if is_gzip:
            buf = io.StringIO(contents)
            f = gzip.GzipFile(fileobj=buf)
            contents = f.read()

        # No docs say I should close this. I can only assume.
        response.close()

        # response.read() returns bytes. Convert to str as soon as possible
        # so the rest of the program can work with str.
        return contents.decode(encoding=self.encoding)

class FeedmeHTMLParser(FeedmeURLDownloader):

    def __init__(self, config, feedname):
        super(FeedmeHTMLParser, self).__init__(config, feedname)

        self.outfile = None
        self.skipping = None
        self.remapped_images = {}
        self.base_href = None
        self.verbose = False

    def fetch_url(self, url, newdir, newname, title=None, author=None,
                  footer='', referrer=None, user_agent=None):
        """Read a URL from the web. Parse it, rewriting any links,
           downloading any images and making any other changes needed
           according to the config file and current feed name.
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

        html = self.download_url(url, referrer, user_agent,
                                 verbose=self.verbose)

        # Does it contain any of skip_content_pats anywhere? If so, bail.
        skip_content_pats = get_config_multiline(self.config, self.feedname,
                                              'skip_content_pats')
        for pat in skip_content_pats:
            if re.search(pat, html):
                raise NoContentError("Skipping, skip_content_pats " + pat)

        outfilename = os.path.join(self.newdir, self.newname)
        self.outfile = open(outfilename, "w")
        self.outfile.write("""<html>\n<head>
<meta http-equiv="Content-Type" content="text/html; charset=%s">
<link rel="stylesheet" type="text/css" title="Feeds" href="../../feeds.css"/>
<title>%s</title>
</head>

<body>
""" % (self.encoding, title))

        if author:
            self.outfile.write("By: %s\n<p>\n" % author)

        # Throw out everything before the first page_start pattern we see,
        # and after the page_end patterns
        page_starts = get_config_multiline(self.config, self.feedname,
                                           'page_start')
        page_ends = get_config_multiline(self.config, self.feedname, 'page_end')
        if len(page_starts) > 0:
            for page_start in page_starts:
                if self.verbose:
                    print("looking for page_start", page_start, file=sys.stderr)
                print("type(html) is", type(html))
                match = html.find(page_start)
                if match >= 0:
                    if self.verbose:
                        print("Found page_start", page_start, file=sys.stderr)
                    html = html[match:]
                    break

        if len(page_ends) > 0:
            for page_end in page_ends:
                if self.verbose:
                    print("looking for page_end", page_end, file=sys.stderr)
                match = html.find(page_end)
                if match >= 0:
                    if self.verbose:
                        print("Found page_end", page_end, file=sys.stderr)
                    html = html[0 : match]

        # Skip anything matching any of the skip_pats.
        # It may eventually be better to do this in the HTML parser.
        skip_pats = get_config_multiline(self.config, self.feedname,
                                         'skip_pats')
        if len(skip_pats) > 0:
            for skip in skip_pats:
                if self.verbose:
                    print("Trying to skip '%s'" % skip, file=sys.stderr)
                    #print >>sys.stderr, "in", html.encode('utf-8')
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
                # Another way would be to use (.|\\n) in place of .
                # For some reason [.\n] doesn't work.
                #html = re.sub(skip, '', html, flags=re.DOTALL)

        # print >>sys.stderr, "After skipping skip_pats, html is:"
        # print >>sys.stderr, html.encode(self.encoding, 'replace')

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
            print("Didn't get any content for", title, file=sys.stderr)
            self.outfile.close()
            os.remove(outfilename)
            raise NoContentError

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
                print(uhtml[:512].encode('utf-8',
                                                       'xmlcharrefreplace'), end=' ', file=sys.stderr)
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
        # And yes, BeautifulSoup would be more straightforward for this task.
        # But we're already using lxml.html for all the rest of the parsing.

        try:
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
                            pass
                        if self.verbose:
                            print("Removing img", e.attrib['src'], file=sys.stderr)
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
            print(traceback.format_exc(), file=sys.stderr)
            return content

    def crawl_tree(self, tree):
        """For testing:
import lxml.html
html = '<html><body onload="" color="white">\n<p>Hi  ! Ma&ntilde;ana!\n<a href="/my/path/to/link.html">my link</a>\n</body></html>\n'
tree = lxml.html.fromstring(html)
"""
        #print "Crawling:", tree.tag, "attrib", tree.attrib
        if type(tree.tag) is str:
            # lxml.html gives comments tag = <built-in function Comment>
            # This is not documented anywhere and there seems to be
            # no way to ask "Is this element a comment?"
            # So we only handle tags that are type str.
            self.handle_starttag(tree.tag, tree.attrib)
            if tree.text:
                #print tree.tag, "contains text", tree.text
                self.handle_data(tree.text)
            for node in tree:
                self.crawl_tree(node)
            self.handle_endtag(tree.tag)
        # print the tail even if it was a comment -- the tail is
        # part of the parent tag, not the current tag.
        if tree.tail:
            #print tree.tag, "contains text", tree.tail
            self.handle_data(tree.tail)

    def handle_starttag(self, tag, attrs):
        #if self.verbose:
        #    print "start tag", tag, attrs

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
                    print("href is '" +  href + "'", file=sys.stderr)
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
            # print "Skipping start tag", tag, "inside a skipped section"
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
            # print >>sys.stderr, "Starting a skippable", tag, "section"
            return

        if self.tag_skippable(tag):
            # print >>sys.stderr, "skipping start", tag, "tag"
            return

        #print "type(tag) =", type(tag)
        self.outfile.write('<' + tag)

        if tag == 'a':
            if 'href' in list(attrs.keys()):
                href = attrs['href']
                #print >>sys.stderr, "a href", href

                # See if this matches the single-page pattern,
                # if we're not already following one:
                if not self.single_page_url:
                    #print "we're not in the single page already"
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

                #print "Rewriting href", href, "to", self.make_absolute(href)
                attrs['href'] = self.make_absolute(href)
            #print "a attrs now are", attrs

        elif tag == 'img' and 'src' in list(attrs.keys()):
            src = attrs['src']

            # Make relative URLs absolute
            src = self.make_absolute(src)
            if not src:
                return

            # urllib2 can't parse out the host part without first
            # creating a Request object:
            req = urllib.request.Request(src)
            req.add_header('User-Agent', self.user_agent)

            # For now, only fetch images that come from the HTML's host:
            try:
                nonlocal_images = self.config.getboolean(self.feedname,
                                                         'nonlocal_images')
            except:
                nonlocal_images = False
            if nonlocal_images or self.same_host(req.host, self.host):
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
                                or x in '-_.='])
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

                try:
                    if not os.path.exists(imgfilename):
                        print("Fetching", src, "to", imgfilename, file=sys.stderr)
                        f = urllib.request.urlopen(req)
                        # Lots of things can go wrong with downloading
                        # the image, such as exceptions.IOError from
                        # [Errno 36] File name too long
                        # XXX Might want to wrap this in its own try.
                        local_file = open(imgfilename, "w")
                        # Write to our local file
                        local_file.write(f.read())
                        local_file.close()
                    #else:
                    #    print "Not downloading, already have", imgfilename

                    # If we got this far, then we have a local image,
                    # so go ahead and rewrite the url:
                    self.remapped_images[src] = base
                    attrs['src'] = base

                # handle download errors
                except urllib.error.HTTPError as e:
                    print("HTTP Error:", e.code, "on", src, file=sys.stderr)
                    # Since we couldn't download, point instead to the
                    # absolute URL, so it will at least work with a
                    # live net connection.
                    attrs['src'] = src
                except urllib.error.URLError as e:
                    print("URL Error:", e.reason, "on", src, file=sys.stderr)
                    attrs['src'] = src
                except Exception as e:
                    print("Error downloading image:", str(e), \
                        "on", src, file=sys.stderr)
                    attrs['src'] = src
            else:
                # Looks like it's probably a nonlocal image.
                # Possibly this could be smarter about finding similar domains,
                # or having a list of allowed image domains.
                print(req.host, "and", self.host,
                      "are too different -- not fetching", file=sys.stderr)

        # Now we've done any needed processing to the tag and its attrs.
        # t's time to write them to the output file.
        for attr in list(attrs.keys()):
            self.outfile.write(' ' + attr)
            if attrs[attr] and type(attrs[attr]) is str:
                # make sure attr[1] doesn't have any embedded double-quotes
                val = attrs[attr].replace('"', '\"')
                self.outfile.write('="' + val + '"')

        self.outfile.write('>')

    def handle_endtag(self, tag):
        #print "end tag", tag
        if tag == self.skipping:
            self.skipping = False
            # print >>sys.stderr, "Ending a skippable", tag, "section"
            return
        if self.skipping:
            # print "Skipping end tag", tag, "inside a skipped section"
            return
        if self.tag_skippable(tag) or self.tag_skippable_section(tag):
            # print >>sys.stderr, "Skipping end", tag
            return

        # Some tags don't have ends, and it can cause problems:
        # e.g. <br></br> displays as two breaks, not one.
        if tag in [ "br", "img" ]:
            return

        # Don't close the body or html -- caller may want to add a footer.
        if tag == "body" or tag == 'html':
            return

        # print >>sys.stderr, "Writing end tag", tag
        self.outfile.write('</' + tag + '>\n')

    def handle_data(self, data):
        # XXX lxml.etree.tostring() might be a cleaner way of printing
        # these nodes: http://lxml.de/tutorial.html
        if self.skipping:
            #print >>sys.stderr, "Skipping data"
            return

        # If it's not just whitespace, make a note that we've written something.
        if data.strip():
            self.wrote_data = True

        if type(data) is str:
            self.outfile.write(data)
        else:
            print("Data isn't str! type =", type(data), file=sys.stderr)

    # def handle_entityref(self, name):
    #     if self.skipping:
    #         #print "Skipping entityref"
    #         return
    #     self.outfile.write('&' + name + ';')

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

        if url[0] == '/':
            return urllib.parse.urljoin(self.prefix, url)

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

        # If we're skipping images, we could either omit them
        # entirely, or leave them in with their src= unchanged
        # so that a network-connected viewer can still fetch them.
        # For now, let's opt to remove them.
        if tag == 'img' and \
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

        return False

def sub_tilde(name):
    """Do what os.path.expanduser does, but also allow $HOME in paths"""
    # config.get alas doesn't substitute $HOME or ~
    if name[0:2] == "~/":
        name = os.path.join(os.environ['HOME'], name[2:])
    elif name[0:6] == "$HOME/":
        name = os.path.join(os.environ['HOME'], name[6:])
    return name

#
# Read the configuration file (don't act on it yet)
#
def read_config_file():
    #
    # Read the config file
    #
    if 'XDG_CONFIG_HOME' in os.environ:
        confdir = os.path.join(os.environ['XDG_CONFIG_HOME'], 'feedme')
    else:
        confdir = os.path.join(os.environ['HOME'], '.config', 'feedme')

    main_conf_file = 'feedme.conf'
    conffile = os.path.join(confdir, main_conf_file)
    if not os.access(conffile, os.R_OK):
        print("Error: no config file in", conffile, file=sys.stderr)
        sys.exit(1)

    config = ConfigParser({'verbose' : 'false',
                           'levels' : '2',
                           'encoding' : '',  # blank means try several
                           'page_start' : '',
                           'page_end':'',
                           'single_page_pats' : '',
                           'url_substitute' : '',

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

                           'nocache' : 'false',
                           'logfile' : '',
                           'save_days' : '7',
                           'skip_images' : 'true',
                           'nonlocal_images' : 'false',
                           'skip_links' : 'false',
                           'when' : '',  # Day, like tue, or month-day, like 14
                           'min_width' : '25', # min # chars in an item link
                           'continue_on_timeout' : 'false',
                           'user_agent' : None,
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

if __name__ == '__main__':
    config = read_config_file()

    parser = FeedmeHTMLParser(config, 'Freaktest')
    parser.fetch_url('http://www.freakonomics.com/2011/12/21/what-to-do-with-cheating-students/', '/home/akkana/feeds/Freaktest/', 'test.html', "Freak Test")

