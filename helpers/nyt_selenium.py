#!/usr/bin/env python3

# A FeedMe helper that can fetch articles from the NYTimes
# using a logged-in subscriber profile and selenium.
# As currently written, it will use the first profile in ~/.mozilla/firefox
# that has "selenium" in the name.
#
# If geckodriver isn't in your path, pass the path to it
# as the helper_arg.

import os, sys

from selenium import webdriver
from selenium.webdriver.firefox.options import Options
from selenium.common import exceptions as selenium_exceptions

from bs4 import BeautifulSoup
import re

import traceback


verbose = True

# The selenium browser driver will be set by initialize()
sbrowser = None


adpat = re.compile("story-ad-[0-9]*-wrapper")


def initialize(helper_arg):
    """Initialize selenium, returning the web driver object."""

    global sbrowser

    foxprofiledir = find_firefox_profile_dir()

    # Deprecated, but no one seems to know the new way:
    options = Options()
    options.headless = True
    print("Creating headless browser...", file=sys.stderr)
    kwargs = {
        "firefox_profile": foxprofiledir,
        "options":         options,
    }

    if helper_arg and (helper_arg.startswith('/')
                       or helper_arg.startswith('~')):
        executable_path = os.path.expanduser(helper_arg)
    else:
        executable_path = "geckodriver"

    sbrowser = webdriver.Firefox(firefox_profile=foxprofiledir,
                                 executable_path=executable_path,
                                 options=options)


def fetch_article(url):
    """Fetch the given article using the already initialized
       selenium browser driver.
       Filter it down using BeautifulSoup so feedme doesn't have to.
    """

    sbrowser.get(url)
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
    sbrowser = None
    sbrowser = init_selenium()
    read_rss(sbrowser, savedir=os.path.expanduser("~/feeds/nyt"))
