#!/usr/bin/env python

# URL parser for feedme, http://shallowsky.com/software/feedme/
# Copyright 2011 by Akkana Peck. Share and enjoy under the GPL v2 or later.

import os, sys
import urllib2
import re
from ConfigParser import ConfigParser
#from HTMLParser import HTMLParser
import lxml.html
import urlparse
from cookielib import CookieJar
import StringIO
import gzip

has_ununicode=True
try:
    import ununicode
except ImportError, e:
    has_ununicode=False

def output_encode(s, encoding):
    if encoding == 'ascii' and has_ununicode:
        #return unicodedata.normalize('NFKD', s).encode('ascii', 'ignore')
        # valid values in encode are replace and ignore
        return ununicode.toascii(s,
                                 in_encoding=encoding,
                                 errfilename=os.path.join(outdir,
                                                          "errors"))
    elif isinstance(s, unicode):
        return s.encode('utf-8', 'backslashreplace')
    else:
        return s

VersionString = "FeedMe 1.0b1"

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
           contents as a string. Allow for possible vagaries like cookies,
           redirection, compression etc.
        """
        if verbose:
            print >>sys.stderr, "download_url", url, "referrer=", referrer, \
                                "user_agent", user_agent

        request = urllib2.Request(url)

        # If we're after the single-page URL, we may need a referrer
        if referrer:
            if verbose:
                print >>sys.stderr, "Adding referrer", referrer
            request.add_header('Referer', referrer)

        if not user_agent:
            user_agent = VersionString
        request.add_header('User-Agent', user_agent)
        if verbose:
            print >>sys.stderr, "Using User-Agent of", user_agent

        # Allow for cookies in the request: some sites, notably nytimes.com,
        # degrade to an infinite redirect loop if cookies aren't enabled.
        cj = CookieJar()
        opener = urllib2.build_opener(urllib2.HTTPCookieProcessor(cj))
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
           and not ctype.startswith("application/atom+xml"):
            print >>sys.stderr, url, "isn't text -- content-type was", \
                ctype, ". Skipping."
            response.close()
            raise RuntimeError("Contents not text (%s)! %s" % (ctype, url))

        # Were we redirected? geturl() will tell us that.
        self.cur_url = response.geturl()

        # but sadly, that means we need another request object
        # to parse out the host and prefix:
        real_request = urllib2.Request(self.cur_url)
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
        self.host = real_request.get_host()
        self.prefix = real_request.get_type() + '://' + self.host + '/'

        # urllib2 unfortunately doesn't read unicode,
        # so try to figure out the current encoding:
        if not self.encoding or self.encoding == '':
            self.encoding = response.headers.getparam('charset')
            #print >>sys.stderr, "getparam charset returned", self.encoding
            enctype = response.headers['content-type'].split('charset=')
            if len(enctype) > 1:
                self.encoding = enctype[-1]
                #print >>sys.stderr, "enctype gave", self.encoding
            else:
                #print >>sys.stderr, "Defaulting to utf-8"
                self.encoding = 'utf-8'
        if verbose:
            print >>sys.stderr, "final encoding is", self.encoding

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
        except Exception, e:
            print >>sys.stderr, "Unknown error from response.read()", url

        # contents can be undefined here. If so, no point in doing anything else.
        if not contents:
            print >>sys.stderr, "Didn't read anything from response.read()"
            response.close()
            raise NoContentError

        if is_gzip:
            buf = StringIO.StringIO(contents)
            f = gzip.GzipFile(fileobj=buf)
            contents = f.read()

        #print >>sys.stderr, "response.read() returned type", type(contents)
        # Want to end up with unicode. In case it's str, decode it:
        if type(contents) is str:
            # But sometimes this raises errors anyway, even using
            # the page's own encoding, so use 'replace':
            contents = contents.decode(self.encoding, 'replace')

        # No docs say I should close this. I can only assume.
        response.close()

        return contents

class FeedmeHTMLParser(FeedmeURLDownloader):

    def __init__(self, config, feedname):
        super(FeedmeHTMLParser, self).__init__(config, feedname)

        self.outfile = None
        self.skipping = None
        self.remapped_images = {}
        self.base_href = None

    def fetch_url(self, url, newdir, newname, title=None, author=None,
                  footer='', referrer=None, user_agent=None):
        """Read a URL from the web. Parse it, rewriting any links,
           downloading any images and making any other changes needed
           according to the config file and current feed name.
           Write the modified HTML output to $newdir/$newname,
           and download any images into $newdir.
           Raises NoContentError if it can't get the page.
        """
        verbose = self.config.getboolean(self.feedname, 'verbose')
        if verbose:
            print >>sys.stderr, "Fetching link", url, \
                "to", newdir + "/" + newname

        self.newdir = newdir
        self.newname = newname
        self.cururl = url
        if type(title) is unicode:
            title = title.encode('utf-8', 'replace')
        if type(author) is unicode:
            author = author.encode('utf-8', 'replace')

        # A flag to indicate when we're skipping everything --
        # e.g. inside <script> tags.
        self.skipping = None

        # Do we need to do any substitution on the URL first?
        urlsub = get_config_multiline(self.config, self.feedname,
                                           'url_substitute')
        if urlsub:
            print >>sys.stderr, "Substituting", urlsub[0], "to", urlsub[1]
            print >>sys.stderr, "Rewriting:", url
            url = re.sub(urlsub[0], urlsub[1], url)
            print >>sys.stderr, "Became:   ", url

        self.encoding = self.config.get(self.feedname, 'encoding')

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

        html = self.download_url(url, referrer, user_agent)

        # Throw out everything before the page_start patterns
        # and after the page_end patterns
        page_starts = get_config_multiline(self.config, self.feedname,
                                           'page_start')
        page_ends = get_config_multiline(self.config, self.feedname, 'page_end')
        if len(page_starts) > 0:
            for page_start in page_starts:
                if verbose:
                    print >>sys.stderr, "looking for page_start", page_start
                match = html.find(page_start)
                if match >= 0:
                    if verbose:
                        print >>sys.stderr, "Found page_start", page_start
                    html = html[match:]
                    break

        if len(page_ends) > 0:
            for page_end in page_ends:
                if verbose:
                    print >>sys.stderr, "looking for page_end", page_end
                match = html.find(page_end)
                if match >= 0:
                    if verbose:
                        print >>sys.stderr, "Found page_end", page_end
                    html = html[0 : match]

        # Skip anything matching any of the skip_pats.
        # It may eventually be better to do this in the HTML parser.
        skip_pats = get_config_multiline(self.config, self.feedname, 'skip_pat')
        if len(skip_pats) > 0:
            for skip in skip_pats:
                if verbose:
                    print >>sys.stderr, "Trying to skip '%s'" % skip
                    #print >>sys.stderr, "in", html.encode('utf-8')
                    #sys.stderr.flush()
                # flags=DOTALL doesn't exist in re.sub until 2.7,
                #html = re.sub(skip, '', html, flags=re.DOTALL)
                # but does exist in a compiled re expression:
                regexp = re.compile(skip, flags=re.DOTALL)
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
            print >>sys.stderr, "Didn't get any content for", title
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
                if verbose:
                    print >>sys.stderr, \
                        "Trying to fetch single-page url with referrer =", \
                        response.geturl(), "instead of", url
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
                    if verbose:
                        print >>sys.stderr, "Removing", outfilename, \
                            "and renaming", singlefile
                else:
                    print >>sys.stderr, \
                        "Tried to fetch single-page file but apparently failed"
            except (IOError, urllib2.HTTPError) as e:
                print >>sys.stderr, "Couldn't read single-page URL", \
                    self.single_page_url
                print >>sys.stderr, e

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
            print "ValueError!"
            # Idiot lxml.html that doesn't give any sensible way
            # to tell what really went wrong:
            if str(sys.exc_info()[1]).startswith(
                "Unicode strings with encoding declaration"):
                # This seems to happen because somehow the html gets
                # something like this inserted at the beginning:
                # <?xml version="1.0" encoding="utf-8"?>
                # So if we've hit the error, try to remove it:
                print >>sys.stderr, "Stupid lxml encoding error on:"
                print >>sys.stderr, uhtml[:512].encode('utf-8',
                                                       'xmlcharrefreplace'),
                print '...'

                # Some sample strings that screw up lxml and must be removed:
                # <?xml version="1.0" encoding="ascii" ?>
                uhtml = re.sub('<\?xml .*?encoding=[\'\"].*?[\'\"].*?\?>',
                                '', uhtml)
                tree = lxml.html.fromstring(uhtml)
                print "Tried to remove encoding: now"
                print >>sys.stderr, uhtml[:512].encode('utf-8',
                                                       'xmlcharrefreplace'),
                print '...'
            else:
                raise ValueError

        # Iterate over the DOM tree:
        self.crawl_tree(tree)

        # Eventually we can print it with lxml.html.tostring(tree)

    def rewrite_images(self, content):
        """Rewrite img src tags to point to local images we downloaded earlier.
           We already rewrote the img tags in the HTML file, but feedme
           may need us to rewrite img tags embedded in the RSS content.
        """
        # And yes, BeautifulSoup would be more straightforward for this task.
        # But we're already using lxml.html for all the rest of the parsing.

        try:
            tree = lxml.html.fromstring(content)
            for e in tree.iter():
                if e.tag == 'img' and 'src' in e.keys():
                    try:
                        src = self.make_absolute(e.attrib['src'])
                        if src in self.remapped_images.keys():
                            e.attrib['src'] = self.remapped_images[src]
                    except KeyError:
                        pass

            return lxml.html.tostring(tree)
        except Exception, e:
            print >>sys.stderr, "Couldn't rewrite images in content:", str(e)
            print >>sys.stderr, "content: '" + content + "'"
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
        #if self.config.getboolean(self.feedname, 'verbose'):
        #    print "start tag", tag, attrs

        # meta refreshes won't work when we're offline, but we
        # might want to display them to give the user the option.
        # <meta http-equiv="Refresh" content="0; URL=http://blah"></meta>
        # meta charset is the other meta tag we care about.
        # All other meta tags will be skipped, so do this test
        # before checking for tag_skippable.
        if tag == 'meta':
            if 'charset' in attrs.keys() and attrs['charset']:
                self.encoding = attrs['charset']
                return
            if 'http-equiv' in attrs.keys() and \
                    attrs['http-equiv'].lower() == 'refresh':
                self.outfile.write("Meta refresh suppressed.<br />")
                if 'content' in attrs.keys():
                    content = attrs['content'].split(';')
                    if len(content) > 1:
                        href = content[1].strip()
                    else:
                        href = content[0].strip()
                    # XXX Next comparison might be better done with re,
                    # in case of spaces around the =.
                    print >>sys.stderr, "href is '" +  href + "'"
                    if href.upper().startswith('URL='):
                        href = href[4:]
                    self.outfile.write('<a href="' + href + '">'
                                       + href + '</a>')

                    # Also set the refresh target as the single_page_url.
                    # Maybe we can actually get it here.
                    if not self.single_page_url:
                        self.single_page_url = \
                            self.make_absolute(href)
                        print >>sys.stderr, \
                            "\nTrying meta refresh as single-page pattern:", \
                            self.single_page_url.encode('utf-8',
                                                        'xmlcharrefreplace')
                return
                # XXX Note that this won't skip the </meta> tag, unfortunately,
                # and tag_skippable_section can't distinguish between
                # meta refresh and any other meta tags.

        if self.skipping:
            # print "Skipping start tag", tag, "inside a skipped section"
            return

        if tag == 'base' and 'href' in attrs.keys():
            self.base_href = attrs['href']
            return

        # Delete any style tags used for color or things like display:none
        if 'style' in attrs.keys():
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
        self.outfile.write('<' + tag.encode(self.encoding, 'xmlcharrefreplace'))

        if tag == 'a':
            if 'href' in attrs.keys():
                href = attrs['href']
                #print >>sys.stderr, "a href", href

                # See if this matches the single-page pattern,
                # if we're not already following one:
                if not self.single_page_url:
                    #print "we're not in the single page already"
                    single_page_pats = get_config_multiline(self.config,
                                                            self.feedname,
                                                            'single_page_pat')
                    for single_page_pat in single_page_pats:
                        m = re.search(single_page_pat, href)
                        if m:
                            self.single_page_url = \
                                self.make_absolute(href[m.start():m.end()])
                            print >>sys.stderr, \
                                "\nFound single-page pattern:", \
                                self.single_page_url.encode('utf-8',
                                                            'xmlcharrefreplace')
                            # But continue fetching the regular pattern,
                            # since the single-page one may fail

                #print "Rewriting href", href, "to", self.make_absolute(href)
                attrs['href'] = self.make_absolute(href)
            #print "a attrs now are", attrs

        elif tag == 'img' and 'src' in attrs.keys():
            src = attrs['src']

            # Make relative URLs absolute
            src = self.make_absolute(src)
            if not src:
                return

            # urllib2 can't parse out the host part without first
            # creating a Request object:
            req = urllib2.Request(src)
            req.add_header('User-Agent', self.user_agent)

            # For now, only fetch images that come from the HTML's host:
            try:
                nonlocal_images = self.config.getboolean(self.feedname,
                                                         'nonlocal_images')
            except:
                nonlocal_images = False
            if nonlocal_images or self.same_host(req.get_host(), self.host):
                base = os.path.basename(src)
                # Clean up the basename, since it might have illegal chars.
                # Only allow alphanumerics or others in a short whitelist.
                # Don't allow % in the whitelist -- it causes problems
                # with recursively copying the files over http later.
                base = ''.join([x for x in base if x.isalpha() or x.isdigit()
                                or x in '-_.='])
                if not base : base = '_unknown.img'
                imgfilename = os.path.join(self.newdir, base)
                try:
                    if not os.path.exists(imgfilename):
                        print >>sys.stderr, "Fetching", src, "to", imgfilename
                        f = urllib2.urlopen(req)
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
                except urllib2.HTTPError, e:
                    print >>sys.stderr, "HTTP Error:", e.code, "on", src
                    # Since we couldn't download, point instead to the
                    # absolute URL, so it will at least work with a
                    # live net connection.
                    attrs['src'] = src
                except urllib2.URLError, e:
                    print >>sys.stderr, "URL Error:", e.reason, "on", src
                    attrs['src'] = src
                except Exception, e:
                    print >>sys.stderr, "Error downloading image:", str(e), \
                        "on", src
                    attrs['src'] = src
            else:
                # Looks like it's probably a nonlocal image.
                # Possibly this could be smarter about finding similar domains,
                # or having a list of allowed image domains.
                print >>sys.stderr, req.get_host(), "and", self.host, "are too different -- not fetching"

        # Now we've done any needed processing to the tag and its attrs.
        # t's time to write them to the output file.
        for attr in attrs.keys():
            self.outfile.write(' ' + attr.encode(self.encoding,
                                                 'xmlcharrefreplace'))
            if attrs[attr] and type(attrs[attr]) is str:
                # make sure attr[1] doesn't have any embedded double-quotes
                val = attrs[attr].replace('"', '\"').encode(self.encoding,
                                                            'xmlcharrefreplace')
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
        self.outfile.write('</' + tag.encode(self.encoding,
                                             'xmlcharrefreplace') + '>\n')

    def handle_data(self, data):
        # XXX lxml.etree.tostring() might be a cleaner way of printing
        # these nodes: http://lxml.de/tutorial.html
        if self.skipping:
            #print >>sys.stderr, "Skipping data"
            return

        # If it's not just whitespace, make a note that we've written something.
        if data.strip():
            self.wrote_data = True

        if type(data) is unicode:
            #print >>sys.stderr, "Unicode data is", \
            #    data.encode(self.encoding, 'xmlcharrefreplace')
            self.outfile.write(data.encode(self.encoding, 'xmlcharrefreplace'))
        elif type(data) is str:
            #print >>sys.stderr, "Writing some ascii data:", data
            self.outfile.write(data)
        else:
            print >>sys.stderr, "Data isn't str or unicode! type =", type(data)

    # def handle_charref(self, num):
    #     # I don't think we ever actually get here -- lxml.html.fromstring()
    #     # already replaces all html entities with the numeric unicode
    #     # equivalent whether we want that or not, and we have to write
    #     # them out in handle_data with xmlcharrefreplace.
    #     # If we really really wanted to we might be able to keep the
    #     # page's original entities by calling fromstring(cgi.urlescape(html))
    #     # html before 
    #     if self.skipping:
    #         #print "Skipping charref"
    #         return
    #     self.outfile.write('&#' + num.encode(self.encoding,
    #                                          'xmlcharrefreplace') + ';')

    # def handle_entityref(self, name):
    #     if self.skipping:
    #         #print "Skipping entityref"
    #         return
    #     self.outfile.write('&' + name.encode(self.encoding,
    #                                          'xmlcharrefreplace') + ';')

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
            return urlparse.urljoin(self.base_href, url)

        if url[0] == '/':
            return urlparse.urljoin(self.prefix, url)

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
        conffile = os.path.join(os.environ['XDG_CONFIG_HOME'],
                                'feedme', 'feedme.conf')
    else:
        conffile = os.path.join(os.environ['HOME'], '.config',
                                'feedme', 'feedme.conf')
    if not os.access(conffile, os.R_OK):
        print >>sys.stderr, "Error: no config file in", conffile
        sys.exit(1)

    config = ConfigParser({'verbose' : 'false',
                           'levels' : '2',
                           'encoding' : '',  # blank means try several
                           'page_start' : '',
                           'page_end':'',
                           'single_page_pat' : '',
                           'url_substitute' : '',
                           'skip_pat' : '',
                           'nocache' : 'false',
                           'logfile' : '',
                           'save_days' : '7',
                           'skip_images' : 'true',
                           'nonlocal_images' : 'false',
                           'skip_links' : 'false',
                           'skip_link_pat' : '',
                           'index_skip_pat' : '',
                           'when' : '',   # Day, like tue, or month day, like 14
                           'min_width' : '25', # min # chars in an item link
                           'continue_on_timeout' : 'false',
                           'user_agent' : None,
                           'ascii' : 'false',
                           'allow_gzip' : 'true'})
    config.read(conffile)
    return config

if __name__ == '__main__':
    config = read_config_file()

    parser = FeedmeHTMLParser(config, 'Freaktest')
    parser.fetch_url('http://www.freakonomics.com/2011/12/21/what-to-do-with-cheating-students/', '/home/akkana/feeds/Freaktest/', 'test.html', "Freak Test")

