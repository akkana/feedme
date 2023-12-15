#!/usr/bin/env python3

# Proof of concept for scraping nytimes.org using a Firefox profile
# that contains login credentials.

# It might be possible to run selenium and firefox
# without installing the whole X stack and running a real X server:
# https://namekdev.net/2016/08/selenium-server-without-x-window-system-xvfb/
# https://stackoverflow.com/questions/6183276/how-do-i-run-selenium-in-xvfb/6300672#6300672
# https://stackoverflow.com/questions/10399557/is-it-possible-to-run-selenium-firefox-web-driver-without-a-gui
# In that case, it would be more reasonable to run this firectly from feedme.

import os, sys

from selenium import webdriver
from selenium.webdriver.firefox.options import Options
from selenium.common import exceptions as selenium_exceptions

from bs4 import BeautifulSoup
import re

import feedparser
from urllib.parse import urlparse
import traceback

# sheesh, this is apparently the recommended way to parse RFC 2822 dates:
# from email.utils as email_utils
from email.utils import parsedate
import time
from datetime import date

# RSS_URL = 'https://rss.nytimes.com/services/xml/rss/nyt/HomePage.xml'
RSS_URL = 'http://localhost/tmp/HomePage.xml'

# Tuning for the NYT
adpat = re.compile("story-ad-[0-9]*-wrapper")

verbose = True


def read_rss(sbrowser=None, savedir=None):
    day = date.today().strftime("%a")
    feed_title = "%s New York Times" % day
    index = """<html>
<head>

<meta name="viewport" content="width=device-width, initial-scale=1">
<title>%s</title>
<link rel="stylesheet" type="text/css" title="Feeds" href="../../feeds.css">
</head>

<body>
<h1>%s</h1>
""" % (feed_title, feed_title)

    feed = feedparser.parse(RSS_URL)

    # feedparser has no error return! One way is to check len(feed.feed).
    if len(feed.feed) == 0:
        print("Couldn't fetch RSS from", RSS_URL, file=sys.stderr)
        return

    articleno = 0
    for item in feed.entries:
        if 'links' in item:
            href = [str(i['href']) for i in item.links
                    if 'rel' in i and 'href' in i
                    and i['rel'] == 'alternate']
        else:
            href = []

        # Get an item ID
        if 'id' in item:
            item_id = str(item.id)
            if verbose:
                print("\nID %s" % item_id, file=sys.stderr)
        elif href:
            item_id = str(href[0])
            if verbose:
                print("Using URL '%s' for ID" % item_id, file=sys.stderr)
        else:
            if verbose:
                print("Item in %s had no ID or URL." % str(href[0]),
                      file=sys.stderr)
                continue  # or return?

        # Get the published date.
        # item.pubDate is a unicode string, supposed to be in format
        # Thu, 11 Aug 2016 14:46:50 GMT (or +0000)
        # email.utils.parsedate returns a tuple.
        # Pass it to time.mktime() to get seconds since epoch,
        # XXX feedparser now has published_parsed which is
        # a time.struct_time. Can we count on that and not
        # have to do parsing here?
        try:
            pub_date = time.mktime(parsedate(item.published))
        except:
            pub_date = None

        if verbose:
            print("pub_date", pub_date)

        if 'author' in item:
            author = str(item.author)
        else:
            author = None
        if verbose:
            print("Author:", author)

        item_link = str(item.link)
        if verbose:
            print("Link:", item_link)
            print(item.summary)

        # Now fetch the item to a local file, if there's a driver
        if not sbrowser or not savedir:
            return

        # On the first article, make the directory if necessary
        try:
            os.makedirs(savedir)
        except FileExistsError:
            pass

        fullhtml = fetch_article_selenium(item_link, sbrowser)
        if not fullhtml:
            print("Couldn't fetch", item_link)
            return
            continue

        soup = BeautifulSoup(fullhtml, "lxml")

        # Try several possible containers
        article = soup.find("section", {"name": "articleBody"})

        if not article:
            print("No articleBody")
            article = soup.find(class_="live-blog-post")
        else:
            print("yay, found articleBody")

        if not article:
            print("No live-blog-post either")
            article = soup
            fullfile = os.path.join(savedir, "%d-full.html" % articleno)
            print("Couldn't find any containers: saving %s" % fullfile)
            with open(fullfile, "w") as fp:
                fp.write(fullhtml)

        # Remove ads, story-ad-*-wrapper
        for ad in article.find_all(class_=adpat):
            ad.decompose()

        # Remove images, for now, until this is folded into the
        # regular feedme/pageparser code to fetch images locally.
        for img in article.find_all("img"):
            img.decompose()
        # NYT has huge SVG images that use the "svg" tag
        for img in article.find_all("svg"):
            img.decompose()

        # Done with processing, ready to write it!
        htmlfile = os.path.join(savedir, "%d.html" % articleno)
        with open(htmlfile, "wb") as outfp:
            outfp.write(article.prettify(encoding='utf-8'))
            print("Wrote", htmlfile)

        try:
            item_title = str(item.title)
        except:
            item_title = "(no title)"

        # Add this article to the index
        index += """<p><a name="%d">&#160;</a><a href="%s"><b>%s</b></a>
<p>
%s
<p>
<br>[[ <a href="%d.html">%s</a> ]]
""" % (articleno, os.path.basename(htmlfile), item_title, item.summary,
       articleno, item_title)

        articleno += 1
        # if articleno > 3:
        #     break

    # Finish the index file and write it.
    index += """</body></html><p><a name="10">&nbsp;</a>

<hr><i>(Downloaded by FeedMe 1.0 NYT fetcher)</i>
"""

    with open(os.path.join(savedir, "index.html"), "w") as indexfp:
        indexfp.write(index)

    print("Wrote", articleno, "files plus index")


def find_firefox_profile_dir():
    """Return the first profile in ~/.mozilla/firefox/
       that has "selenium" in its name.
    """
    mozdir = os.path.expanduser("~/.mozilla/firefox/")
    for pdir in os.listdir(mozdir):
        if "selenium" in pdir:
            return os.path.join(mozdir, pdir)
    raise RuntimeError("Can't find a selenium profile in %s" % pdir)


def fetch_article_selenium(article_url, sbrowser):
    """Fetch a list of articles taken from the RSS.
       Takes a list of urlsand a directory to store the result.
       Saves each article as savedir/basename(url).
    """
    try:
        sbrowser.get(article_url)
        return sbrowser.page_source

    except Exception as e:
        print("*** Exception:", e)
        traceback.print_exc()
        return None

"""
        # Try to get the article body.
        article = None
        for container in nytcontainers:
            try:
                article = sbrowser.find_element_by_name(container)
                break
            except selenium_exceptions.NoSuchElementException:
                pass

        if not article:
            article = sbrowser.page_source
            print(article_url, ": Writing full page source to /tmp/pagesource")
            with open("/tmp/pagesource", "w") as outfp:
                outfp.write(article)

        # or tag name, sbrowser.find_element_by_tag_name("section")
        # for matching multiple conditions, xpath might be useful:
        # email_input = sbrowser.find_element_by_xpath("//input[@name='email']")
        # However, selenium can't actually modify the content of anything,
        # so it's probably best to punt and do the rest in BeautifulSoup.

        return article.get_attribute('innerHTML')
"""


def init_selenium():
    """Initialize selenium, returning the web driver object."""

    foxprofiledir = find_firefox_profile_dir()

    # Deprecated, but no one seems to know the new way:
    options = Options()
    options.headless = True
    print("Creating headless browser...")
    sbrowser = webdriver.Firefox(firefox_profile=foxprofiledir,
                                options=options)

    # Another way, also deprecated:
    # profile = webdriver.FirefoxProfile(profile_directory=foxprofiledir)
    # sbrowser = webdriver.firefox.webdriver.WebDriver(firefox_profile=profile)

    # sbrowser.get('https://nytimes.com/')
    # sbrowser.get('https://www.nytimes.com/2021/09/30/opinion/andrew-yang.html')

    return sbrowser


if __name__ == '__main__':
    sbrowser = None
    sbrowser = init_selenium()
    read_rss(sbrowser, savedir=os.path.expanduser("~/feeds/nyt"))
