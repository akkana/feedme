#!/usr/bin/env python

import urllib2
import lxml.html
import sys, os

# Are we on Android? Make it optional, for easier testing.
try:
    import android
    is_android = True
except:
    is_android = False

if is_android:
    droid = android.Android()

def perror(s):
    if is_android:
        droid.makeToast(s)
    print s

def fetch_url_to(url, outfile, return_contents=False):
    print "Fetching", url, "to", outfile

    # Read the URL
    infile = urllib2.urlopen(url)
    contents = infile.read()
    infile.close()

    # Copy to the output file
    outf = open(outfile, 'w')
    outf.write(contents)
    outf.close()

    # Return the contents if needed
    if return_contents:
        return contents

def fetch_feed_dir(dirurl, outdir):
    '''Fetch index.html inside the given url, plus everything it points to.'''

    if not dirurl.endswith('/'):
        dirurl += '/'

    print "Fetch_feed_dir", dirurl, outdir

    # Make sure the directory exists
    print "Trying to create", outdir
    if not os.access(outdir, os.W_OK):
        os.makedirs(outdir, 0755)

    index = fetch_url_to(dirurl + 'index.html',
                         os.path.join(outdir, 'index.html'), True)

    tree = lxml.html.fromstring(index)
    for element, attribute, link, pos in tree.iterlinks():
        # Only fetch local URLs.
        print "  link", link
        if not link or ':' in link or link[0] == '/' or '#' in link \
                or link.startswith('../'):
                # I don't know why we see ../ links, but we do.
            continue
        fetch_url_to(dirurl + link, os.path.join(outdir, link), False)

def fetch_dir_recursive(urldir, outdir):
    if not urldir.endswith('/'):
        urldir += '/'
    print urldir, "should end with a slash now"

    print "Reading directory entries for", urldir
    f = urllib2.urlopen(urldir)
    dirpage = f.read()
    # dirlines = dirpage.split('\n')
    f.close()

    # Parse the directory contents to get the list of feeds
    # It's in a table tag, where valid entries will look like:
    # <tr><td valign="top"><img src="/icons/folder.gif" alt="[DIR]"></td><td><a href="BBC_News_Science/">BBC_News_Science/</a></td><td align="right">28-Jun-2013 18:42  </td><td align="right">  - </td><td>&nbsp;</td></tr>
    # for line in dirlines:
    #     if '<img src="/icons/folder.gif"' not in line:
    #         continue    # Not a subdirectory
    print "Parsing links..."
    feeddirs = []
    try :
        tree = lxml.html.fromstring(dirpage)
        for element, attribute, link, pos in tree.iterlinks():
            # Save only links to files, not .. or the various sort commands
            if element.tag == 'a' and attribute == 'href' \
                    and link[0] != '?' and link[0] != '/':
                print "Appending", link
                feeddirs.append(link)
            else: print "Not appending", element, attribute, link

    except Exception, e:
        perror("Couldn't parse directory page: " + e)
        return

    # now feeddirs[] should contain the subdirs we want to fetch.
    print "Will try to fetch feed dirs:", feeddirs
    print

    for subdir in feeddirs:
        fetch_feed_dir(urldir + subdir, os.path.join(outdir, subdir))

dirdate = '06-28-Fri'
baseurl = 'http://shallowsky.com/feeds/' + dirdate
fetch_dir_recursive(baseurl, '/tmp/newdir')

