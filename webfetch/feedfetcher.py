#!/usr/bin/env python

import urllib2

import BeautifulSoup
# http://www.crummy.com/software/BeautifulSoup/bs3/documentation.html
import HTMLParser  # Needed for the exceptions used by BeautifulSoup

import sys, os
import time

# Are we on Android? Make it optional, for easier testing.
try:
    import android
    print "Running on Android"
    is_android = True
    droid = android.Android()
    droid.makeToast("Running on Android!")
except ImportError:
    print "Not running on Android"
    is_android = False

def perror(s):
    if is_android:
        droid.makeToast(s)
    print s

def fetch_url_to(url, outfile):
    if os.path.exists(outfile):
        print outfile, "already exists -- not re-fetching"
        return

    print "Fetching", url, "to", outfile

    # Read the URL
    infile = urllib2.urlopen(url)
    contents = infile.read()
    infile.close()

    # Copy to the output file
    outf = open(outfile, 'w')
    outf.write(contents)
    outf.close()

    if not url.lower().endswith('.html'):
        return

    # If html, go through the contents and recursively fetch
    # any local links.
    try:
        soup = BeautifulSoup.BeautifulSoup(contents)
    except HTMLParser.HTMLParseError, e:
        perror("Parse error on " + url + "! " + str(e))
        print "First part of file was:", contents[:256]
        return

    def not_a_local_link(l):
        if not link or ':' in link or '#' in link or link[0] == '/' \
                or link.startswith('../'):
                # I don't know why we see ../ links, but we do.
            return True
        return False

    dirurl = os.path.dirname(url) + '/'
    outdir = os.path.dirname(outfile)

    for tag in soup.findAll('a'):
        # BeautifulSoup doesn't support calls like 'href' in tag
        try:
            link = tag['href']
        except KeyError:
            continue
        if not_a_local_link(link):
            continue

        fetch_url_to(dirurl + link, os.path.join(outdir, link))

    for tag in soup.findAll('img'):
        # BeautifulSoup doesn't support calls like 'href' in tag
        #print "Checking image", tag
        try:
            link = tag['src']
        except KeyError:
            continue
        if not_a_local_link(link):
            #print "Not a local link:", link
            continue
        fetch_url_to(dirurl + link, os.path.join(outdir, link))

def fetch_feed_dir(dirurl, outdir):
    '''Fetch index.html inside the given url, plus everything it points to.'''

    if not dirurl.endswith('/'):
        dirurl += '/'

    print "Fetch_feed_dir", dirurl, outdir

    # Make sure the directory exists
    if not os.access(outdir, os.W_OK):
        os.makedirs(outdir, 0755)

    index = fetch_url_to(dirurl + 'index.html',
                         os.path.join(outdir, 'index.html'))

def fetch_dir_recursive(urldir, outdir):
    if not urldir.endswith('/'):
        urldir += '/'

    f = urllib2.urlopen(urldir)
    dirpage = f.read()
    # dirlines = dirpage.split('\n')
    f.close()

    # Parse the directory contents to get the list of feeds
    # It's in a table tag, where valid entries will look like:
    # <tr><td valign="top"><img src="/icons/folder.gif" alt="[DIR]"></td><td><a href="BBC_News_Science/">BBC_News_Science/</a></td><td align="right">28-Jun-2013 18:42  </td><td align="right">  - </td><td>&nbsp;</td></tr>
    feeddirs = []
    try :
        soup = BeautifulSoup.BeautifulSoup(dirpage)
    except HTMLParser.HTMLParseError, e:
        perror("Couldn't parse directory page " + urldir + str(e))
        print "Parsed HTML began:", dirpage[:256]
        return

    for tag in soup.findAll('a'):
        try:
            link = tag['href']
        except KeyError:
            continue
        if link[0] != '?' and link[0] != '/':
            feeddirs.append(link)

    # now feeddirs[] should contain the subdirs we want to fetch.
    print "Will try to fetch feed dirs:", feeddirs
    print

    for subdir in feeddirs:
        fetch_feed_dir(urldir + subdir, os.path.join(outdir, subdir))

dirdate = time.strftime("%m-%d-%a"))
baseurl = 'http://shallowsky.com/feeds/' + dirdate
if is_android:
    outdir = '/mnt/sdcard/external_sd/feeds/'
else:
    outdir = '/tmp/feeds'
outdir = os.path.join(outdir, dirdate)
fetch_dir_recursive(baseurl, outdir)

if is_android:
    droid.makeToast("Feeds downloaded")
    droid.vibrate()
    droid.notify('Feed Fetcher', 'Feeds downloaded to ' + outdir)

print 'Feeds downloaded to ' + outdir

