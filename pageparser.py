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
import traceback

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
       If there are no network errors but the content is empty after any
       substitutes (or just empty to begin with), raises NoContentError.
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
            with open(filename, encoding='utf-8') as fp:
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
            # Used to raise a RuntimeError here -- but then the feed
            # (especially Xtra) ends up empty with no indication why.
            # Instead, return a simple string explaining the problem
            # raise RuntimeError("Contents not text (%s)! %s" % (ctype, url))
            print("Contents not text (%s)! %s" % (ctype, url), file=sys.stderr)
            return '<p>Contents not text! (%s) <a href="%s">%s</a></p>' \
                % (ctype, url, url)

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
        # pagemeparser will need them.
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
        # But save a string telling the user there was a problem
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
            print("UnicodeDecodeError on", self.cur_url, file=sys.stderr)
            return contents.decode(encoding=self.encoding,
                                   errors="backslashreplace")
        except Exception as e:
            s = "Unknown error trying to decode %s: %s" % (self.cur_url, e)
            print(s, file=sys.stderr)
            return s


class FeedmeHTMLParser(FeedmeURLDownloader):

    def __init__(self, feedname):
        super(FeedmeHTMLParser, self).__init__(feedname)

        self.outfile = None
        self.skipping = None
        self.base_href = None

        self.multipages = []

    def fetch_url(self, url, newdir, newname, title=None, author=None,
                  html=None,
                  footer='', referrer=None, user_agent=None,
                  sub_page=False):
        """Read a URL from the web. Parse it, rewriting any links,
           downloading any images and making any other changes needed
           according to the config file and current feed name.
           If the optional argument html contains a string,
           skip the downloading and use the html provided.
           Write the modified HTML output to newdir/newname
           (unless newname is None, in which case just return the html)
           and download any images into $newdir.
           If sub_page is true, then it will append to an existing file
           rather than replacing it.
           Raises NoContentError if it can't get the page or skipped it.
        """
        self.verbose = utils.g_config.getboolean(self.feedname, 'verbose')
        if self.verbose:
            if html:
                print("Parsing html from index,", len(html),
                      "chars from to", url,
                      "to", newdir + "/" + newname, file=sys.stderr)
            elif newname:
                print("Fetching link", url,
                      "to", newdir + "/" + newname,
                      "sub_page=", sub_page,
                      file=sys.stderr)
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

        # In case download_url didn't get anything:
        if not html:
            html = '<h1>No article</h1>\n<p>No HTML downloaded!\n"

        # Does it contain any of skip_content_pats anywhere? If so, bail.
        skip_content_pats = utils.g_config.get_multiline(self.feedname,
                                                 'skip_content_pats')
        for pat in skip_content_pats:
            if re.search(pat, html):
                raise NoContentError("Skipping, skip_content_pats " + pat)

        if self.newname:
            outfilename = os.path.join(self.newdir, self.newname)

            if sub_page:
                self.outfile = open(outfilename, "a", encoding=self.encoding)
            else:
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

""" % (self.encoding, title))
        else:
            outfilename = None
            self.outfile = io.StringIO()

        #
        # First, some operations on the HTML source,
        # like regexps that match patterns in the source.
        # The edited source will later be parsed by BeautifulSoup.
        #

        if author:
            body = html.find('<body>')
            if body >= 0:
                html = html[0:body] + "By: %s\n<p>\n" % author \
                    + html[body:]

        # Throw out everything before the first page_start re pattern seen,
        # and after the first page_end pattern seen.
        page_starts = utils.g_config.get_multiline(self.feedname, 'page_start')
        page_ends = utils.g_config.get_multiline(self.feedname, 'page_end')

        if len(page_starts) > 0:
            for page_start in page_starts:
                if self.verbose:
                    print("looking for page_start", page_start, file=sys.stderr)
                start_re = re.compile(page_start, flags=re.DOTALL|re.IGNORECASE)
                match = start_re.search(html)
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
                end_re = re.compile(page_end, flags=re.DOTALL|re.IGNORECASE)
                match = end_re.search(html)
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

        # Keep a record of whether we've seen any content:
        self.wrote_data = False

        # Delete any nodes specified for skipping
        html = delete_skipped_nodes(html, self.feedname)

        # Iterate through the HTML, making any necessary simplifications:
        try:
            self.handle_html(html, title, footer)
        except Exception as e:
            if self.verbose:
                print("error in handle_html:", e, file=sys.stderr)
                traceback.print_exc(file=sys.stderr)
                traceback.print_stack(limit=6, file=sys.stderr)
            # We're in trouble here, but try to write some indication
            # of the error to the outfile.
            try:
                print("error in handle_html:", e, file=self.outfile)
                traceback.print_exc(file=self.outfile)
                self.wrote_data = True
                self.outfile.close()
            except Exception as e:
                print("Couldn't save handle_html error in output file"
                      " because:", e,
                      file=sys.stderr)

        # handle_html() should have closed the file, but if it bombed out
        # early it might not have.
        try:
            self.outfile.close()
        except:
            pass

        # Did we write anything real, any real content?
        # XXX Currently this requires text, might want to add img tags.
        if not self.wrote_data:
            errstr = "No real content"
            print(errstr, file=sys.stderr)
            if self.verbose:
                print("No content, removing", outfilename, file=sys.stderr)
            os.remove(outfilename)
            raise NoContentError(errstr)

        # Now we've fetched the normal URL.

        # Did we see a single-page link? If so, move the fetched
        # file aside and call ourselves recursively to try to fetch
        # the single-page.
        if not self.wrote_data:
            # Don't look for single page or multipage if there wasn't a story
            pass
        elif self.single_page_url and self.single_page_url != url:
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

        # Are there multiple pages? Try to fetch them.
        elif self.multipages and not sub_page:  # XXXXXXXXXXXXXX
            if self.multipages:
                if self.verbose:
                    print("Chasing", len(self.multipages), "extra pages",
                          file=sys.stderr)
                for href in self.multipages:
                    try:
                        # href is the link to this page.
                        # Fetch the content, append it to the current file
                        if self.verbose:
                            print("Recursively fetching next page", href,
                                  file=sys.stderr)
                        self.fetch_url(href, newdir, newname,
                                       title=title, author=author,
                                       html=None,
                                       footer=footer, referrer=referrer,
                                       user_agent=user_agent,
                                       sub_page=True)
                    except Exception as e:
                        print("Couldn't parse", link, ":", e, file=sys.stderr)
                        continue

        if not outfilename and type(self.outfile) is io.StringIO():
            return self.outfile.getvalue()

    def handle_html(self, uhtml, title=None, footer=''):
        """Parse the given unicode as HTML and make all needed substitutions.
           Append the footer if any, write the resulting <body>
           to self.outfile, then close the outfile.
           (The caller has already opened the file and written a header.
           XXX should handle the header here too, for consistency.)
        """

        if not uhtml:
            print("Eek, null HTML passed to handle_html", file=sys.stderr)
            print("Eek, null HTML passed to handle_html", file=self.outfile)
            return
        soup = BeautifulSoup(uhtml, features='lxml')
        if not soup:
            print("Eek, null soup in handle_html", file=sys.stderr)
            print("Eek, null soup in handle_html", file=self.outfile)
            return

        # Does the page have an H1 header already? If not, manufacture one.
        if title and not soup.h1:
            h1 = soup.new_tag("h1")
            soup.body.insert(0, h1)
            h1.append(title)

        # Tags to remove, but keep children if any
        for tagname in [
                # Don't want embedded <head> stuff
                # Unfortunately, skipping the <head> means we miss
                # meta and base. Missing meta is a problem because it
                # means we don't get the charset. XXX But note: we
                # probably won't see the charset anyway, because we'll
                # look for it in the first head, the one we create ourselves,
                # rather than the one that comes from the original page.
                # We really need to merge the minimal information from the page
                # head into the generated one.
                # Meanwhile, these tags may do more harm than good.
                # We definitely need to remove <link type="text/css".
                "head",

                # Omit form elements, since it's too easy to land on
                # them accidentally when scrolling and trigger an
                # unwanted Android onscreen keyboard:
                "form", "input", "textarea",

                # font tags are almost always to impose colors that
                # only work against certain backgrounds
                "font",

                # Omit iframes -- they badly confuse Android's WebView
                # (goBack fails if there's an iframe anywhere in the page:
                # you have to goBack multiple times, I think once for every
                # iframe in the page, and this doesn't seem to be a bug
                # that's getting fixed any time soon).
                # We don't want iframes in simplified HTML anyway.
                "iframe",

                # assorted other unhelpful tags.
                # object is probably flash or video or some such.
                "source", "video", "object",
                "meta", "link",
        ]:
            for t in soup.find_all(tagname):
                t.replace_with_children()

        # Tags to remove entirely along with all children
        for tagname in [
                # disallow scripts
                "script",

                # style tags are often evil MS-Word crap
                "style",

                # The source tag is used to specify alternate forms of media.
                # But the LA Daily Post uses it for images, and many browsers
                # including Android WebView use it to override the img src.
                # So leaving in the source tag may cause images to be fetched
                # from the net rather than from the locally fetched files.
                "source",

                # Skip videos regardless of the skip_images setting,
                # since there's no mechanism to download videos,
                # and including them inline leads to unwanted data charges.
                # Some day maybe this could be a separate pref.
                "video",

                # <link rel="stylesheet" isn't always in the head.
                # Undark puts them at the end of the document but they still
                # apply to the whole document, making text unreadable.
                # I don't know of any other legitimate uses for <link>
                # so let's just remove them all.
                "link",

                ]:
            for t in soup.find_all(tagname):
                t.decompose()

        # <base> tags can confuse the HTML displayer program
        # into looking remotely for images we've copied locally,
        # so remove them.
        # But it might be useful to save the base.
        for t in soup.find_all("base"):
            if "href" in t.attrs:
                self.base_href = t.attrs["href"]
                t.decompose()

        # Remove img if skipping images
        if utils.g_config.getboolean(self.feedname, 'skip_images'):
            for tagname in [ "img", "svg", "figure" ]:
                for t in soup.find_all(tagname):
                    t.decompose()

        # embedded body tags often have unfortunate color settings.
        # Embedded <html> tags don't seem to do any harm, but seem wrong.
        # Keep the first one.
        for tagname in [ "html", "body" ]:
            for i, t in enumerate(soup.find_all(tagname)):
                if i > 0:
                    t.replace_with_children()
        # meta refreshes won't work when we're offline, but we
        # might want to display them to give the user the option.
        # <meta http-equiv="Refresh" content="0; URL=http://blah"></meta>
        # meta charset is the other meta tag we care about.
        # All other meta tags will be skipped, so do this test
        # before checking for tag_skippable.
        for meta in soup.find_all("meta"):
            if 'charset' in meta.attrs and meta.attrs['charset']:
                self.encoding = meta.attrs['charset']

            if 'http-equiv' in meta.attrs and \
               meta.attrs['http-equiv'].lower() == 'refresh':
                self.outfile.write("Meta refresh suppressed.<br />")
                if 'content' in meta.attrs:
                    content = meta.attrs['content'].split(';')
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
                # XXX Note that this won't skip the </meta> tag, unfortunately,
                # and doesn't distinguish meta refresh from any other meta tags.

        if utils.g_config.getboolean(self.feedname, 'skip_links'):
            for t in soup.find_all("a"):
                t.replace_with_children()

        # Look for a tags matching the single-page pattern,
        # if we're not already following one.
        if not self.single_page_url:
            # print("we're not in the single page already")
            single_page_pats = utils.g_config.get_multiline(self.feedname,
                                                            'single_page_pats')
            for single_page_pat in single_page_pats:
                singlepage = soup.find("a", href=single_page_pat)
                if singlepage:
                    self.single_page_url = imagecache.make_absolute(
                        singlepage.href)
                    if self.verbose:
                        print("\nFound single-page pattern:", \
                              self.single_page_url, file=sys.stderr)
                        # But continue fetching the regular pattern,
                        # since the single-page one may fail
                        break


        # Try to make links absolute.
        for t in soup.find_all("a"):
            try:
                abs_href = imagecache.make_absolute(t.href, self.base_href)
                if abs_href != t.href:
                    t.href = abs_href
            except:
                continue

        # Now crawl all tags removing any style= attribute
        for t in soup.find_all(style=True):
            style = t.attrs['style']
            if 'color' in style or 'background' in style:
                del t.attrs["style"]

        # Finally, handle images
        for tagname in [ "img", "svg" ]:
            for t in soup.find_all(tagname):
                try:
                    imagecache.process_img_tag(t, self.feedname,
                                               self.base_href, self.newdir)
                except Exception as e:
                    print("Error handling image tag", t, ":", e,
                          file=sys.stderr)
                    utils.ptraceback()

        # find out if there will be a need to look for subsequent pages
        multipage_pat = utils.g_config.get(self.feedname, "multipage_pat",
                                           fallback=None)
        if multipage_pat:
            links = soup.find_all('a', href=re.compile(multipage_pat))
            self.multipages = [ a.attrs['href'] for a in links ]

            # Eliminate duplicates
            self.multipages = list(dict.fromkeys(self.multipages))
            if self.verbose:
                print("Found multipage links:", file=sys.stderr)
                for l in self.multipages:
                    print("   ", l, file=sys.stderr)
        else:
            self.multipages = None

        # Done with processing! Write the soup's body to self.outfile.
        pretty = soup.body.prettify()
        if pretty:
            if footer:
                # pretty already ends with </body>, so find the last
                # occurrence of </body> and prepend the footer.
                # Anything after the last </body> will be lost,
                # but there shouldn't be anything there.
                spl = pretty.rsplit('</body>', 1)
                pretty = spl[0] + footer + '\n</body>\n</html>\n'
            self.outfile.write(pretty)
            self.wrote_data = True
        else:
            print("Empty body! Not writing", file=sys.stderr)


def delete_skipped_nodes(html, feedname):
    """If skip_nodes is set for this feed, remove any matching nodes
       from the HTML, returning rewritten HTML.
    """
    skip_nodespecs = utils.g_config.get_multiline(feedname,
                                                  'skip_nodes')
    if not skip_nodespecs:
        return html

    soup = BeautifulSoup(html, "lxml")

    changed = False
    for nodespec in skip_nodespecs:
        print("looking for skip_node", nodespec, file=sys.stderr)

        # Syntax is something like: div class="sticky-box"
        # first word should be node type,
        # which may be followed by someattr="somename"
        try:
            nodename, attrname, attrval = \
                re.match(SKIP_NODE_PAT, nodespec).groups()
            print((f"nodename '{nodename}', "
                   f"attrname '{attrname}', "
                   f"attrval='{attrval}'"), file=sys.stderr)

            # attrval is a regexp, which BS won't notice unless
            # it's already compiled.
            attrval = re.compile(attrval)
            for node in soup.find_all(nodename,
                                      attrs={ attrname: attrval }):
                print("  Found a node:", node)
                node.decompose()
                changed = True
        except Exception as e:
            print("Problem finding SKIP_NODE_PAT '%s': %s"
                  % (nodespec, e), file=sys.stderr)
            utils.ptraceback()
            continue
    if changed:
        print("Changed nodes in the HTML: rewriting", file=sys.stderr)
        return str(soup)
    else:
        return html


def simplify_html(inhtml):
    """Simplify HTML to contain only a very few tags.
       Used for the text in the blurbs in each site toplevel page,
       on sites that put the whole article in the RSS and so
       need to be truncated.
    """
    soup = BeautifulSoup(inhtml, "lxml")
    for tag in soup.body.find_all():
        if "style" in tag.attrs:
            del tag.attrs["style"]
    return soup.prettify()


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


