#!/usr/bin/env python3

# A FeedMe helper that can fetch articles from the NYTimes
# using a logged-in subscriber profile and selenium.
# As currently written, it will use the first profile in ~/.mozilla/firefox
# that has "selenium" in the name.
#
# If geckodriver isn't in your path, pass the path to it
# as the helper_arg.

# XXX Need to set log_path, otherwise it uses . even if unwritable.

from selenium import webdriver
from selenium.webdriver.firefox.options import Options
from selenium.webdriver.common.desired_capabilities import DesiredCapabilities
# from selenium.common import exceptions as selenium_exceptions

from bs4 import BeautifulSoup

import os, sys

import re
import time

import traceback


verbose = True

# The selenium browser driver will be set by initialize()
sbrowser = None


adpat = re.compile("story-ad-[0-9]*-wrapper")


def initialize(helper_arg=None):
    """Initialize selenium, returning the web driver object."""

    global sbrowser

    foxprofiledir = find_firefox_profile_dir()

    # Deprecated, but no one seems to know the new way:
    options = Options()
    options.headless = True

    # Don't wait for full page load, but return as soon as the
    # page would normally be usable.
    caps = DesiredCapabilities().FIREFOX
    caps["pageLoadStrategy"] = "eager"  #  interactive
    # Other options are "normal" (full page load) or "none" (?)

    print("Creating headless browser...", file=sys.stderr)

    # With the default of "geckodriver", selenium will search $PATH.
    executable_path = "geckodriver"
    if helper_arg and (helper_arg.startswith('/')
                       or helper_arg.startswith('~')):
        arg_path = os.path.expanduser(helper_arg)

        # Did this point to the actual geckodriver executable?
        # If so, pass it as executable_path.
        if arg_path.endswith("geckodriver") and os.path.exists(arg_path) \
           and os.path.isfile(arg_path):
            executable_path - arg_path
        elif os.path.isdir(arg_path) and \
             os.path.isfile(os.path.join(arg_path, "geckodriver")):
            # It's a directory. Add it to the beginning of $PATH.
            os.environ["PATH"] = "%s:%s" % (arg_path, os.environ["PATH"])

    sbrowser = webdriver.Firefox(firefox_profile=foxprofiledir,
                                 executable_path=executable_path,
                                 options=options)

    # Some people say this is how to set the timeout, others say it fails.
    # sbrowser.set_page_load_timeout(30)


def fetch_article(url):
    """Fetch the given article using the already initialized
       selenium browser driver.
       Filter it down using BeautifulSoup so feedme doesn't have to.
    """

    # While debugging: keep track of how long each article takes.
    t0 = time.time()

    sbrowser.get(url)

    print("%.1f seconds for %s" % (time.time() - t0, url), file=sys.stderr)

    fullhtml = sbrowser.page_source

    if not fullhtml:
        print("nyt_selenium: couldn't fetch", url)
        return None

    soup = BeautifulSoup(fullhtml, "lxml")

    # Look for several possible containers
    article = soup.find("section", {"name": "articleBody"})
    if not article:
        if verbose:
            print("No articleBody", file=sys.stderr)
        article = soup.find(class_="live-blog-post")

    if not article:
        if verbose:
            print("No live-blog-post either.", file=sys.stderr)
        article = soup
        fullfile = os.path.join("/tmp/%d-full.html" % articleno)
        print("Couldn't find any containers: saving %s" % fullfile,
              file=sys.stderr)
        with open(fullfile, "w") as fp:
            fp.write(fullhtml)

    # Remove ads, story-ad-*-wrapper
    for ad in article.find_all(class_=adpat):
        ad.decompose()

    # Remove images, for now, until this is folded into the
    # regular feedme/feedmeparser code to fetch images locally.
    for img in article.find_all("img"):
        img.decompose()
    # NYT has huge SVG images that use the "svg" tag
    for img in article.find_all("svg"):
        img.decompose()

    # Done with processing.
    # BS randomly sometimes returns str, sometimes bytes when encoding
    # is specified. Supposedly if no encoding is specified, it will
    # always return str, which is what's wanted here.
    # return article.prettify(encoding='utf-8')
    return article.prettify()


def find_firefox_profile_dir():
    """Return the first profile in ~/.mozilla/firefox/
       that has "selenium" in its name.
    """
    mozdir = os.path.expanduser("~/.mozilla/firefox/")
    for pdir in os.listdir(mozdir):
        if "selenium" in pdir:
            return os.path.join(mozdir, pdir)
    raise RuntimeError("Can't find a selenium profile in %s" % pdir)


if __name__ == '__main__':
    import feedparser
    import sys

    initialize()

    if len(sys.argv) > 1:
        RSS_URL = sys.argv[1]
    else:
        RSS_URL = 'https://rss.nytimes.com/services/xml/rss/nyt/HomePage.xml'

    feed = feedparser.parse(RSS_URL)

    # feedparser has no error return! One way is to check len(feed.feed).
    if len(feed.feed) == 0:
        print("Couldn't fetch RSS from", RSS_URL, file=sys.stderr)
        sys.exit(1)

    for item in feed.entries:
        if 'links' not in item:
            print("Item with no links! Continuing")
            continue

        lasttime = time.time()

        # href = [str(link['href']) for link in item.links
        #         if 'rel' in link and 'href' in link
        #         and link['rel'] == 'alternate']

        item_link = str(item.link)
        sys.stdout.flush()
        print("\n==========================================")
        print("Link:", item_link)
        print(item.summary)

        fullhtml = fetch_article(item_link)
        if not fullhtml:
            print("Couldn't fetch", item_link)
            continue

        print("full html had", len(fullhtml), "characters")

        thistime = time.time()
        print("Took", thistime - lasttime, "seconds")
        lasttime = thistime
