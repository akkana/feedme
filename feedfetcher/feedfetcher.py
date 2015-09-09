#!/usr/bin/env python

# Initiate feedme on a remote web server;
# wait for it to finish, then download the feeds to the local filesystem.
# Can run either on a local Linux machine or on Android under SL4A.

import urllib, urllib2

import BeautifulSoup
# http://www.crummy.com/software/BeautifulSoup/bs3/documentation.html
import HTMLParser  # Needed for the exceptions used by BeautifulSoup

import sys, os
import time

# import traceback

############# CONFIGURATION ########################################

# Put your server base URL here, the dir that will contain
# both feedme/ and feeds/ directories. It must end with a slash.
serverurl = 'http://localhost/'

# Where to download feeds if running locally.
# This may include ~ for home directory.
localdir = '~/feeds'

# Where to download feeds if running on Android.
# Should be an absolute path, probably starting with /mnd/sdcard.
android_localdir = '/mnt/sdcard/external_sd/feeds/'

############# END CONFIGURATION ####################################

errstr = ''

# Are we on Android? Make it optional, for easier testing.
try:
    import android
    print "Running on Android"
    is_android = True
    droid = android.Android()
    #droid.makeToast("Running on Android!")
except ImportError:
    print "Not running on Android"
    is_android = False

def perror(s):
    global errstr
    # makeToast is cool but takes way too long and might still not be seen.
    #if is_android:
    #    droid.makeToast(s)
    print s
    errstr += '\n' + s

def fetch_url_to(url, outfile):
    if os.path.exists(outfile):
        print os.path.basename(outfile), "already exists -- not re-fetching"
        return

    # If there's a named anchor appended, strip it from both url and filename.
    # Pro Publica in particular has a lot of these.
    if '#' in url:
        print "Stripping named anchor from url", url
        url = url[:url.find('#')]
    if '#' in outfile:
        print "Stripping named anchor from outfile", outfile
        outfile = outfile[:outfile.find('#')]

    # If there's a % in the url, e.g. if there's a filename with a space
    # like file%20name.jpg (Los Alamos Daily Post loves to do this),
    # apache on the server end will urldecode it.
    # Tried encoding the % to %25 to prevent that from happening,
    # and that prevents throwing error but the photos still don't download.
    # It's best to make sure feedme doesn't save any URLs like that
    # in the first place.
    url = url.replace('%', '%25')

    print "Fetching", url, "to", outfile

    # Read the URL. It may fail: not all referenced links
    # were successfully downloaded to the server.
    # But sometimes it fails for other reasons too,
    # so we need to distinguish a "not found" from other causes.
    try:
        infile = urllib2.urlopen(url)
        contents = infile.read()
        infile.close()
    except urllib2.HTTPError, e:
        perror("Couldn't fetch %s: HTTPError code %s" % (url, str(e.code)))
        return
    except urllib2.URLError, e:
        perror("Couldn't fetch %s: URLError args %s" % (url, str(e.args)))
        return
    except ValueError, e:
        perror("Couldn't fetch %s: ValueError, %s" % (url, str(e)))
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
        '''If a link doesn't have a schema, consists only of an
           absolute path or a named anchor like #, it's local.
        '''
        if not link or ':' in link or link.startswith('#') or link[0] == '/' \
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

        # Lots of pages have recursive links to themselves.
        # Obviously no point in fetching those.
        if os.path.join(outdir, link) != outfile:
            fetch_url_to(dirurl + link, os.path.join(outdir, link))

    for tag in soup.findAll('img'):
        # BeautifulSoup doesn't support calls like 'href' in tag
        #print "Checking image", tag
        try:
            link = tag['src']
        except KeyError:
            continue
        if not_a_local_link(link):
            continue
        fetch_url_to(dirurl + link, os.path.join(outdir, link))

def fetch_feed_dir(dirurl, outdir):
    '''Fetch index.html inside the given url, plus everything it points to.'''

    if not dirurl.endswith('/'):
        dirurl += '/'

    print "Fetch_feed_dir", dirurl, outdir

    # Make sure the directory exists
    if not os.access(outdir, os.W_OK):
        try:
            os.makedirs(outdir, 0755)
        except Exception as e:
            perror("Couldn't make directory %s" % outdir)
            raise e

    index = fetch_url_to(dirurl + 'index.html',
                         os.path.join(outdir, 'index.html'))
    
def parse_directory_page(urldir):
    '''Parse a directory page from the server, returning a list of subdirs.
       Return None implies there was a problem reaching the urldir.
       Return of [] means the urldir was there, but contains no subdirs.
    '''
    if not urldir.endswith('/'):
        urldir += '/'

    try:
        f = urllib2.urlopen(urldir)
        dirpage = f.read()
        # dirlines = dirpage.split('\n')
        f.close()
    except urllib2.HTTPError, e:
        # This always happens the first time, but if it happens
        # repeatedly then it indicates a problem.
        perror("HTTP error parsing directory page %s: code is %d" \
                % (urldir, e.code))
        return None

    # Parse the directory contents to get the list of feeds
    # It's in a table tag, where valid entries will look like:
    # <tr><td valign="top"><img src="/icons/folder.gif" alt="[DIR]"></td><td><a href="BBC_News_Science/">BBC_News_Science/</a></td><td align="right">28-Jun-2013 18:42  </td><td align="right">  - </td><td>&nbsp;</td></tr>
    feeddirs = []
    try:
        soup = BeautifulSoup.BeautifulSoup(dirpage)
    except HTMLParser.HTMLParseError, e:
        perror("Couldn't parse directory page " + urldir + str(e))
        print "Parsed HTML began:", dirpage[:256]
        return feeddirs

    for tag in soup.findAll('a'):
        try:
            link = tag['href']
        except KeyError:
            continue
        if link[0] != '?' and link[0] != '/':
            feeddirs.append(link)

    return feeddirs

def fetch_feeds_dir_recursive(urldir, outdir):
    feeddirs = parse_directory_page(urldir)
    if feeddirs == None:
        err = "Couldn't find %s on server" % os.path.basename(urldir)
        perror(err)
        if is_android:
            droid.vibrate()
            droid.notify(err)
        return

    # now feeddirs[] should contain the subdirs we want to fetch.
    print "Will try to fetch feed dirs:", feeddirs
    print

    for subdir in feeddirs:
        # Don't fetch the log file
        if subdir == 'LOG':
            continue
        fetch_feed_dir(urldir + subdir, os.path.join(outdir, subdir))

def run_feed(serverurl, outdir):
    # Hit the CGI URL on the server to tell it to run feedme.
    # First build up the URL with any extra URLs we've collected:
    url = os.path.join(serverurl + 'feedme/urlrss.cgi?xtraurls=')
    savedpath = os.path.join(outdir, 'saved-urls')
    saved_urls = []
    try:
        saved = open(savedpath)
        for line in saved:
            print line,
            saved_urls.append(urllib.quote_plus(line.strip()))
        saved.close()
        # Now rename the saved file so we won't get those urls again.
        bakpath = savedpath + '.bak'
        if os.path.exists(bakpath):
            os.unlink(bakpath)
        os.rename(savedpath, bakpath)
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
    # This may throw various HTTP errors.
    infile = urllib2.urlopen(url)
    contents = infile.read()
    infile.close()
    print "Read:"
    print contents

    # Now, supposedly, feedme is running on the server.

def url_exists(url):
    '''Does the URL exist? Return True or False.'''
    try:
        urlfile = urllib2.urlopen(url)
        urlfile.close()
        return True
    except urllib2.HTTPError, e:
        if e.code == 404:
            return False
        perror("HTTP error checking whether URL %s exists! code %d" % (url, e.code))
        raise(e)

    except urllib2.URLError, e:
        # There is NO documentation on how to handle URLErrors
        # or how to find out the code,
        # but from fiddling around, it's a a socket.gaierror,
        # which can be treated as a tuple where the 0th element is the errno.
        perror("URL error checking whether URL %s exists! errno %d" \
                   % (url, e.reason[0]))
        # Was it because we don't have a network at all?
        # 2 = failure in address resolution, -2 = name or service not known.
        # If it's one of these, give the user a chance to notice it and
        # restart the network.
        if e.reason[0] == 2 or e.reason[0] == -2:
            droid.vibrate()
            droid.makeToast("Network may be down!")
            return False
        raise(e)

    # We can also get various other errors, such as httplib.BadStatusLine
    except Exception, e:
        perror("Problem checking whether URL %s exists!\nException: %s" \
                   % (url, str(e)))
        raise(e)

def wait_for_feeds(baseurl):
    # When the server is done running feedme, it should create a file
    # inside the date directory called LOG.
    # So look for that:
    logurl = baseurl + 'LOG'
    print "Waiting for LOG to appear at", logurl, '...'
    save_feeddirs = []
    while not url_exists(logurl):
        print '.',
        sys.stdout.flush()

        # Check for new directories appearing in the feeds dir,
        # and print them out as they appear.
        feeddirs = parse_directory_page(baseurl)
        # Can't do a simple set(feeddirs) - set(save_feeddirs) here:
        # one of them might be None and that throws an error.
        if feeddirs:
            newdirs = set(feeddirs)
            if save_feeddirs:
                newdirs -= set(save_feeddirs)
            for newfeed in newdirs:
                print newfeed,
            save_feeddirs = feeddirs
        else:
            print "(no feeddirs yet)",

        time.sleep(10)

def download_feeds(baseurl, outdir):
    fetch_feeds_dir_recursive(baseurl, outdir)

    if is_android:
        droid.makeToast("Feeds downloaded")
        droid.vibrate()
        droid.notify('Feed Fetcher', 'Feeds downloaded to ' + outdir)

    print 'Feeds downloaded to ' + outdir

def check_if_feedme_run(feedurl, dateurl):
    '''Only initiate a feed if there isn't already a log file there,
       either in the base dir (meaning a feed is still running)
       or in any subdir (meaning it ran and has finished).
       feedurl and dateurl are both expected to end with / already.
       We might have aborted some earlier attempt, or even kicked
       off feeds from some other machine, but now need to download feeds.
       Return 0 if we think feedme has not yet run, 1 if we think it
       is still running, 2 if we think it has finished.
    '''

    if url_exists(feedurl + 'LOG'):
        return 1                     # still running

    if url_exists(dateurl + 'LOG'):
        return 2                     # ran and is now finished

    return 0                         # has not yet run

if __name__ == '__main__':
    if is_android:
        outdir = android_localdir
    else:
        outdir = os.path.expanduser(localdir)
    dirdate = time.strftime("%m-%d-%a")

    feedurl = serverurl + 'feeds/'
    baseurl = feedurl + dirdate + '/'

    already_ran = check_if_feedme_run(feedurl, baseurl)
    if already_ran == 0:
        print "Feedme has not yet run"
    elif already_ran == 1:
        print "Feedme is running already"
    else:
        print "Feedme already ran to completion"
    sys.stdout.flush()

    try:
        if already_ran == 0:
            print "Initiating feedme:", serverurl
            run_feed(serverurl, outdir)

        if already_ran < 2:
            wait_for_feeds(baseurl)

        download_feeds(baseurl, os.path.join(outdir, dirdate))

    except KeyboardInterrupt:
        print "KeyboardInterrupt"
    except urllib2.URLError, e:
        print "Couldn't access server: " + str(e)

    if errstr:
        print "\n\n====== ERRORS ============"
        print errstr
