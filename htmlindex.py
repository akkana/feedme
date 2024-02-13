#!/usr/bin/env python3

"""For sites that don't have RSS, this module pulls links from an
   HTML page according to the specification in html_index_links,
   which is a string indicating which HTML tag and attributes to look for,
   e.g. 'div class="layout-homepage__lite"'
   Multiple attributes are allowed.
"""

from datetime import datetime, timedelta
import urllib.request, urllib.parse
import shlex
import ast
from bs4 import BeautifulSoup
import feedparser
import email.utils as email_utils
from types import SimpleNamespace
import sys

from feedme import pageparser, utils


def parse(feedname, html_index_links, verbose=True):
    """Parse an HTML page and try to extract all the links matching
       the string html_index_links.
       Return a feed object, with structure similar to what
       feedparser returns.
    """
    identifiers = shlex.split(html_index_links)
    tagname = None
    attrs = {}
    for ident in identifiers:
        if '=' in ident:
            name, val = ident.split('=')
            try:
                val = ast.literal_eval(val)
            except ValueError:
                pass
            attrs[name] = val
        else:
            tagname = ident

    downloader = pageparser.FeedmeURLDownloader(feedname)
    feedurl = utils.g_config.get(feedname, 'url')
    soup = BeautifulSoup(downloader.download_url(feedurl), 'lxml')

    # May need to splice feedurl onto the beginning of other URLs later
    feedurlparts = urllib.parse.urlparse(feedurl)

    # feed needs to be accessible via dot notation as feed.feed
    # to match expectations based on feedparser.
    feedret = SimpleNamespace(
        encoding='utf-8',
        feed=SimpleNamespace(title=feedname),
        entries=[]
    )

    # In feed.entries, feedme uses: link links id title author published
    #                               summary|content|description
    # Generally it uses them like: if 'link' in entry: link = entry.link
    # so the object has to handle both "in" and dot notation.
    #
    # What is the difference between item["link"] and item["links"] ?
    # link is a single URL;
    # links is a list of dictionaries with 'href',
    # 'rel'(='alternate'), 'type'(='text/html')
    # item.pubDate is a unicode string, supposed to be in format
    # Thu, 11 Aug 2016 14:46:50 GMT (or +0000)
    # but feedme actually uses published,
    # pub_date = time.mktime(email_utils.parsedate(item.published))
    # feedparser has added published_parsed which is a time.struct_time,
    # but feedme doesn't count on that. Probably best to provide all three.

    # I haven't found a BS find syntax that lets the tag name
    # be optional.
    if tagname:
        finder = soup.find_all(tagname, attrs=attrs)
    else:
        finder = soup.find_all(attrs=attrs)
    for container in finder:
        for link in container.find_all('a', href=True):
            linktext = link.text.strip()
            linkhref = link.get('href')

            urlparts = urllib.parse.urlparse(linkhref)
            if not urlparts.scheme:
                linkhref = urllib.parse.urljoin(feedurl, linkhref)

            # IMPORTANT: With FeedParserDict, you can set either
            # fpd.id = 123 or fpd['id'] = 123, but YOU SHOULD ONLY USE THE
            # SECOND FORM. If you use dot notation to set an attribute,
            # that attribute will NOT create a corresponding dict-style
            # attribute, so 'id' in fpd will still be false.
            thisentry = feedparser.util.FeedParserDict()
            thisentry['id'] = linkhref
            thisentry['link'] = linkhref
            thisentry['links'] = [ linkhref ]
            thisentry['title'] = linktext

            # Summary has to be some sort of object where summary.value
            # is the linktext, so feedme can look at feed.feed.summary.value
            thisentry['summary'] = SimpleNamespace(value=linktext)

            # Try to get the last modified date. Some websites have
            # last-modified, CNN has X-Last-Modified, possibly this
            # list will have to grow.
            lastmodheaders = [ 'last-modified', 'X-Last-Modified' ]
            conn = urllib.request.urlopen(linkhref)
            lastmodstr = None
            lastmod = None
            for lmh in lastmodheaders:
                try:
                    lastmodstr = conn.headers.get(lmh)
                    if lastmodstr:
                        # print("Last mod string", lastmodstr, "for", linkhref)
                        break
                except:
                    pass
            if lastmodstr:
                # sites mostly seem to use format
                # 'Sun, 11 Feb 2024 20:34:41 GMT'
                # XXX for now, let's hope that's always true.
                try:
                    lastmod = datetime.strptime(
                        lastmodstr, '%a, %d %b %Y %H:%M:%S %Z').astimezone()
                except Exception as e:
                    print("htmlindex: couldn't parse '%s': %s"
                          % (lastmodstr, e), file=sys.stderr)
            if not lastmod:
                # Set it to a bit under a week ago
                lastmod = datetime.now() - timedelta(days=5)
            if not lastmodstr:
                lastmodstr = lastmod.strftime('%a, %d %b %Y %H:%M:%S %Z')

            # Now both lastmod and lastmodstr are set.
            thisentry['published'] = lastmodstr
            thisentry['published_parsed'] = lastmod.timetuple()

        # feedret["entries"].append(thisentry)
        feedret.entries.append(thisentry)

    return feedret


if __name__ == '__main__':
    utils.read_config_file()

    from pprint import pprint
    pprint(parse('CNN', 'li class="card--lite"'))

