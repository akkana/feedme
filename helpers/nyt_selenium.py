#!/usr/bin/env python3

# A FeedMe helper that can fetch articles from the NYTimes
# using a logged-in subscriber profile and selenium.
# As currently written, it will use the first profile in ~/.mozilla/firefox
# that has "selenium" in the name.
#
# If geckodriver isn't in your path, pass the path to it
# as the helper_arg.

# XXX Ctrl-C seems to kill the selenium webdriver,
# so all subsequent fetches will fail. Is there a solution?
# Sometimes it hangs forever and ctrl-C is the only solution.

from selenium import webdriver
from selenium.webdriver.firefox.options import Options
from selenium.webdriver.common.desired_capabilities import DesiredCapabilities
# from selenium.common import exceptions as selenium_exceptions
from urllib3.exceptions import MaxRetryError, NewConnectionError
from selenium.common.exceptions import TimeoutException

from bs4 import BeautifulSoup

import os, sys

import re
import time
import tempfile

import traceback


verbose = True

# The selenium browser driver will be set by initialize()
sbrowser = None

# On the NYT, once anything times out, all subsequent stories will also
# time out, and none of the ways that supposedly control selenium
# timeouts actually work, so they'll all wait for several minutes.
# Returning None or an empty string will keep the URL out of the
# cache, which isn't good because it means the same URL will keep
# being tried and timing out every day forever.
# Instead, for NYT timeouts, let's return an HTML snippet
# indicating a timeout, so feedme will mark it as cached
# and won't keep retrying.
num_timeouts = 0

# How many timeouts will be tolerated before giving up?
MAX_TIMEOUTS = 3


adpat = re.compile("story-ad-[0-9]*-wrapper")


def initialize(helper_args=None):
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

    # Unpack the helper_args. Currently support executable_path
    # (which has already been $d expanded, but not ~ expanded)
    # and log_file.
    # With the default of "geckodriver", selenium will search $PATH.
    executable_path = "geckodriver"
    log_file = None
    if helper_args:
        if "executable_path" in helper_args:
            executable_path = os.path.expanduser(helper_args["executable_path"])

        if "log_file" in helper_args:
            log_file = os.path.expanduser(helper_args["log_file"])
            # $d or $f in log_file could fail because the feeds/datedir
            # hasn't yet been created. So create it if needed.
            logdir = os.path.dirname(log_file)
            if not os.path.exists(logdir):
                os.makedirs(logdir)
                print("Created", logdir, "for log file", file=sys.stderr)
            if not os.path.exists(logdir):
                print("Couldn't create %s: storing log in /tmp" % logdir,
                      file=sys.stderr)
                log_file = None

    if not log_file:
        log_file = tempfile.mkstemp(prefix="nyt_geckodriver", suffix=".log")

    if executable_path.startswith('/'):
        # Did this point to the actual geckodriver executable?
        # If so, pass it as executable_path.
        if executable_path.endswith("geckodriver") \
           and os.path.exists(executable_path) \
           and os.path.isfile(executable_path):
            executable_path = executable_path
        elif os.path.isdir(executable_path) and \
             os.path.isfile(os.path.join(executable_path, "geckodriver")):
            # It's a directory. Add it to the beginning of $PATH.
            os.environ["PATH"] = "%s:%s" % (executable_path,
                                            os.environ["PATH"])
            # Reset the executable path we'll pass in to webdriver.Firefox
            # back to the default, since it only needs to search PATH
            executable_path = "geckodriver"
        # XXX No way (yet) to support adding two paths if firefox and
        # geckodriver are in two different places.
        # Should allow dir1:dir2, but then we'd have to check all
        # of them to see if geckodriver exists.

    if verbose:
        print("nyt_selenium: executable_path '%s', log_file '%s'"
              % (executable_path, log_file), file=sys.stderr)

    sbrowser = webdriver.Firefox(firefox_profile=foxprofiledir,
                                 executable_path=executable_path,
                                 service_log_path=log_file,
                                 options=options)

    # Attempt to limit the timeout.
    # None of these reliably limits the timeout, however:
    # sometimes selenium will wait minutes for each story
    # and I haven't found any way around that.
    sbrowser.set_page_load_timeout(25)
    sbrowser.implicitly_wait(20);
    sbrowser.set_script_timeout(20);


def timeout_boilerplate(url, errstr):
    """If there have been too many timeouts, return an HTML page saying so.
    """
    return """<html>
<head><title>Timeout on %s</title></head>
<body>
<h1>Timeout</h1>

<pre>
%s
</pre>

<p>
URL was: <a href="%s">%s</a>
</body>
</html>
""" % (errstr, url, url)


def fetch_article(url):
    """Fetch the given article using the already initialized
       selenium browser driver.
       Filter it down using BeautifulSoup so feedme doesn't have to.
       Return html source as a string, or None.
    """
    global num_timeouts

    # Was there a timeout earlier? Then everything subsequent will fail,
    # so don't even bother trying to get it.
    if num_timeouts >= MAX_TIMEOUTS:
        return timeout_boilerplate(url, "Gave up after earlier timeout")

    # While debugging: keep track of how long each article takes.
    t0 = time.time()

    try:
        sbrowser.get(url)
    except TimeoutException as e:
        num_timeouts += 1
        # Supposedly this sometimes helps in recovering from timeouts.
        # But in practice, nothing helps: once anything times out,
        # every subsequent story also times out.
        # sbrowser.back()
        print("EEK! TimeoutException", e, file=sys.stderr)
        return timeout_boilerplate(url, "TimeoutException")
    except (ConnectionRefusedError, MaxRetryError, NewConnectionError) as e:
        # MaxRetryError and NewConnectionError come from urllib3.exceptions
        # ConnectionRefusedError is a Python builtin.
        num_timeouts += 1
        print("EEK! Connection error", e, file=sys.stderr)
        traceback.print_exc(file=sys.stderr)
        return timeout_boilerplate(url, str(e))
    except Exception as e:
        num_timeouts += 1
        errstr = "Unexpected exception in webdriver.get: " + str(e)
        print(errstr, file=sys.stderr)
        return timeout_boilerplate(url, errstr)

    # Hitting ^C will mess up the webdriver and cause all subsequent
    # stories to fail. Not particularly recommended, but sometimes
    # there's no alternative.
    except KeyboardInterrupt:
        # sbrowser.back()
        num_timeouts += 1
        return timeout_boilerplate(url, "Keyboard Interrupt")

    t1 = time.time()
    print("%.1f seconds for %s" % (t1 - t0, url), file=sys.stderr)

    try:
        fullhtml = sbrowser.page_source
    except Exception as e:
        num_timeouts += 1
        errstr = "Fetched page but couldn't get html: " + str(e)
        print(errstr, file=sys.stderr)
        return timeout_boilerplate(url, errstr)
    print("%.1f seconds to get page_source" % (time.time() - t1),
          file=sys.stderr)

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
