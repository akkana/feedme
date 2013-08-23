#!/usr/bin/env python

import urllib, urllib2

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

    # Read the URL. It may fail: not all referenced links
    # are always successfully downloaded.
    try:
        infile = urllib2.urlopen(url)
        contents = infile.read()
        infile.close()
    except urllib2.HTTPError:
        perror("Couldn't fetch " + url)
        return

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

    try:
        f = urllib2.urlopen(urldir)
        dirpage = f.read()
        # dirlines = dirpage.split('\n')
        f.close()
    except urllib2.HTTPError:
        errstr = "Couldn't find %s on server" % os.path.basename(urldir)
        perror(errstr)
        if is_android:
            droid.vibrate()
            droid.notify(errstr)

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
        # Don't fetch the log file
        if subdir == 'LOG':
            continue
        fetch_feed_dir(urldir + subdir, os.path.join(outdir, subdir))

def run_feed(outdir):
    # Hit the CGI URL on the server to tell it to run feedme.
    # First build up the URL with any extra URLs we've collected:
    url = os.path.join('http://localhost/feedme/urlrss.cgi?xtraurls=')
    savedpath = os.path.join(outdir, 'saved-urls')
    saved_urls = []
    try:
        saved = open(savedpath)
        for line in saved:
            print line,
            saved_urls.append(urllib.quote_plus(line.strip()))
        saved.close()
    except:
        print 'No saved urls'

    if saved_urls:
        #url += '?xtraurls=' + '%0a'.join(saved_urls)
        url += '%0a'.join(saved_urls)
    else:
        url += "none"

    print 'Requesting URL:', url

    # Now hit the URL. We don't actually care about what it returns,
    # though we do care if it throws an error.
    try:
        infile = urllib2.urlopen(url)
        contents = infile.read()
        infile.close()
        print "Read:"
        print contents
    except Exception, e:
        print "Couldn't access server: " + str(e)
        return None

    # Now, supposedly, feedme is running on the server.
    dirdate = time.strftime("%m-%d-%a")
    return dirdate

def wait_for_feeds(baseurl):
    # When the server is done running feedme, it should create a file
    # inside the date directory called LOG.
    # So look for that:
    logurl = baseurl + 'LOG'
    print "Waiting for LOG to appear at", logurl, '...'
    while True:
        try:
            logfile = urllib2.urlopen(logurl)
            logfile.close()
            print "Got it!"
            return
        except urllib2.HTTPError, e:
            if e.code != 404:
                print "\nOops, got some HTTP error other than a 404"
                raise(e)
        print '.',
        sys.stdout.flush()
        time.sleep(5)

def download_feeds(baseurl, outdir):
    fetch_dir_recursive(baseurl, outdir)

    if is_android:
        droid.makeToast("Feeds downloaded")
        droid.vibrate()
        droid.notify('Feed Fetcher', 'Feeds downloaded to ' + outdir)

    print 'Feeds downloaded to ' + outdir

if __name__ == '__main__':
    if is_android:
        outdir = '/mnt/sdcard/external_sd/feeds/'
    else:
        outdir = '/tmp/feeds'
    dirdate = run_feed(outdir)
    if dirdate:
        baseurl = 'http://localhost/feeds/' + dirdate + '/'
        wait_for_feeds(baseurl)
        download_feeds(baseurl, os.path.join(outdir, dirdate))
