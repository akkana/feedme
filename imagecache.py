#!/usr/bin/env python3

"""An image cache for a single feedme site,
   mapping original relative or absolute URLs to a local path.

   Also handles image-specific manipulations like translating SRCSET
   and various weirdo wordpress plugin javascript image swapping quirks.
"""

import utils

import re
import urllib.request, urllib.parse, urllib.error
from bs4 import BeautifulSoup
from PIL import Image, UnidentifiedImageError
import sys, os


# A record of the images downloaded so far
ImageCache = {}


def clear():
    """Clear the image cache, when starting a new site"""
    ImageCache = {}


def rewrite_images(html, baseurl, outdir, feedname, host=None):
    """Process all images referenced in an html file.
    """
    if not host:
        host = urllib.parse.urlparse(baseurl).hostname

    soup = BeautifulSoup(html, "lxml")
    for img in soup.find_all("img"):
        process_img_tag(img, feedname, baseurl, outdir, host=host)
    return str(soup)


def process_img_tag(tag, feedname, base_href, newdir, host=None):
    """Process an img tag (BeautifulSoup) and its attributes.
       Try to detect stand-ins for src, like srcset and various
       weirdo wordpress plugin attributes.

       Download a local copy of the image (if not already downloaded),
       and set the tag to point to it.

       Arguments:
         tag: the img tag, which will be modified in place
         feedname: the name of the current feed
         base_href: the href from which we'll get the host
         host: if there's a need to override the host in base_href
         newdir: the local directory in which this site is being written
    """
    attrs = tag.attrs
    keys = list(attrs.keys())

    if not host:
        host = urllib.parse.urlparse(base_href).hostname

    # Handle both src and srcset.
    # Los Alamos Daily Post has bogus src="data:..." URLs that don't
    # display anything, and the real src is in data-src. Go figure.
    if 'data-src' in keys:
        src = attrs['data-src']
    elif 'src' in keys:
        src = attrs['src']
    else:
        src = None

    if 'srcset' in keys or 'data-lazy-srcset' in keys:
        # The intent here:
        # If there's a srcset, pick the largest one that's still
        # under max_srcset_size, and set src to that.
        # That's what we'll try to download.
        # Then remove the srcset attribute.
        try:
            maximgwidth = int(utils.g_config.get(feedname, 'max_srcset_size'))
        except:
            maximgwidth = 800

        # ladailypost (which I think is wordpress) has a crazy setup
        # where they set the src to something that isn't an image,
        # then have data-lazy-src and/or data-lazy-srcset
        # which presumably get loaded later with JavaScript.
        if 'data-lazy-srcset' in attrs:
            srcset = parse_srcset(attrs['data-lazy-srcset'])
        elif 'srcset' in attrs:
            srcset = parse_srcset(attrs['srcset'])
        else:
            srcset = None

        if srcset:
            try:
                curimg = None
                curwidth = 0
                for pair in srcset:
                    w = pair[1].strip().lower()
                    if not w.endswith('w'):
                        # It probably ends in x and is a resolution-based
                        # descriptor. We don't handle those yet,
                        # but just in case we don't see any width-based
                        # ones, let's save the image.
                        if not curimg:
                            curimg = pair[0].strip()
                        continue
                    w = int(w[:-1])
                    if w > curwidth and w <= maximgwidth:
                        curwidth = w
                        curimg = pair[0].strip()
                if curimg:
                    src = curimg
            except:
                # Wired sometimes has srcset with just a single url
                # that's the same as the src=. In that case it
                # wouldn't do us any good anyway.
                # They also have srcSet="".
                # And there are all sorts of nutty and random things
                # sites do with srcset.
                print("Error parsing srcset: '%s'" % str(srcset),
                      file=sys.stderr)
                pass

    if not src:
        # Don't do anything to this image, it has no src or srcset.
        return

    if 'srcset' in keys:
        del tag.attrs['srcset']

    # Make relative URLs absolute
    if src.startswith("data:"):
        # With a data: url we already have all we need
        return
    src = make_absolute(src, base_href)
    if not src:
        print("make_absolute returned null", file=sys.stderr)
        return

    # urllib2 can't parse out the host part without first
    # creating a Request object.
    # Quote it to guard against URLs with nonascii characters,
    # which will make urllib.request.urlopen bomb out with
    # UnicodeEncodeError: 'ascii' codec can't encode character.
    # If this proves problematical, try the more complicated
    # solution at https://stackoverflow.com/a/40654295
    # req = urllib.request.Request(urllib.parse.quote(src, safe=':/'))
    # lareporter has a new Wordpress plugin that puts images on i0.wp.com
    # with a bunch more characters that now need not to be quoted:
    req = urllib.request.Request(urllib.parse.quote(src, safe=':/?=&%'))
    user_agent = utils.g_config.get(feedname, 'user_agent')
    req.add_header('User-Agent', user_agent)

    # Should we only fetch images that come from the HTML's host?
    try:
        nonlocal_images = utils.g_config.getboolean(feedname, 'nonlocal_images')
    except:
        nonlocal_images = False

    # Should we rewrite images that come from elsewhere,
    # to avoid unwanted data use?
    try:
        block_nonlocal = utils.g_config.getboolean(feedname,
                                                   'block_nonlocal_images')
    except:
        block_nonlocal = False

    # If we can't or won't download an image, what should
    # we replace it with?
    if block_nonlocal:
        # print("Using bogus image source for nonlocal images",
        #       file=sys.stderr)
        alt_src = 'file:///nonexistant'
        # XXX Would be nice, in this case, to put a link around
        # the image so the user could tap on it if they wanted
        # to see it, at least if it isn't already inside a link.
        # That would be easy in BeautifulSoup but it's hard
        # with this start/end tag model.
    else:
        alt_src = src

    alt_domains = utils.g_config.get_multiline(feedname, 'alt_domains')
    if nonlocal_images or similar_host(req.host, host, alt_domains):
        # base = os.path.basename(src)
        # For now, don't take the basename; we want to know
        # if images are unique, and the basename alone
        # can't tell us that.
        base = src.replace('/', '_')
        # Clean up the filename, since it might have illegal chars.
        # Only allow alphanumerics or others in a short whitelist.
        # Don't allow % in the whitelist -- it causes problems
        # with recursively copying the files over http later.
        base = ''.join([x for x in base if x.isalpha() or x.isdigit()
                        or x in '-_.'])
        if not base : base = '_unknown.img'
        imgfilename = os.path.join(newdir, base)

        # Some sites, like High Country News, use the same image
        # name for everything (e.g. they'll have
        # storyname-0418-jpg/image, storyname-0418-jpg/image etc.)
        # so we can't assume that just because the basename is unique,
        # the image must be.
        # if os.path.exists(imgfilename) and \
        #    src not in ImageCache:
        #     howmany = 2
        #     while True:
        #         newimgfile = "%d-%s" % (howmany, imgfilename)
        #         if not os.path.exists(newimgfile):
        #             imgfilename = newimgfile
        #             break
        #         howmany += 1
        # But we don't need this clause if we use the whole image path,
        # not just the basename.

        # Check again for a data: URL, in case it came in
        # from one of the src substitutions.
        # If so, output it and return.
        if src.startswith("data:"):
            return

        try:
            if not os.path.exists(imgfilename):
                print("Fetching image", src, "to", imgfilename,
                      file=sys.stderr)
                # urllib.request.urlopen is supposed to have
                # a default timeout, but if so, it must be
                # many minutes. Try this instead.
                # Timeout is in seconds.
                f = urllib.request.urlopen(req, timeout=8)
                # Lots of things can go wrong with downloading
                # the image, such as exceptions.IOError from
                # [Errno 36] File name too long
                # XXX Might want to wrap this in its own try.
                local_file = open(imgfilename, "wb")
                # Write to our local file
                local_file.write(f.read())
                local_file.close()
            #else:
            #    print("Not downloading, already have", imgfilename)

        # handle download errors
        except urllib.error.HTTPError as e:
            print("HTTP Error on image:", e.code,
                  "on", src, ": setting img src to", alt_src,
                  file=sys.stderr)
            # Since we couldn't download, point instead to the
            # absolute URL, so it will at least work with a
            # live net connection.
            tag.attrs['src'] = alt_src
        except urllib.error.URLError as e:
            print("URL Error on image:", e.reason,
                  "on", src, file=sys.stderr)
            tag.attrs['src'] = alt_src
        except Exception as e:
            print("Error downloading image:", str(e), \
                "on", src, file=sys.stderr)
            utils.ptraceback()
            tag.attrs['src'] = alt_src

        # If we got this far, then we have a local image.

        # Are we resizing large images? Some sites have crazy-big
        # images, like 5328 x 3996, which make no sense whatsoever
        # to view on a phone.
        try:
            maxsize = int(utils.g_config.get(feedname, 'max_image_size'))
            if maxsize and imgfilename and os.path.exists(imgfilename):
                try:
                    im = Image.open(imgfilename)

                    oldwidth, oldheight = im.size
                    if max(oldwidth, oldheight) > maxsize:
                        if oldwidth >= oldheight:    # landscape
                            newwidth = maxsize
                            newheight = oldheight * maxsize // oldwidth
                        else:                        # portrait
                            newheight = maxsize
                            newwidth = oldwidth * maxsize // oldheight
                        print("Resizing %dx%x image to %dx%d" % (oldwidth,
                                                                 oldheight,
                                                                 newwidth,
                                                                 newheight),
                              file=sys.stderr)
                        im = im.resize((newwidth, newheight))

                        # XXX resizing is nice, but LA Daily Post has taken
                        # to using PNG for all their images, making them HUGE
                        # so translating to JPG would also be good.
                        # 3 ways to check for transparency:
                        # https://stackoverflow.com/questions/43864101/python-pil-check-if-image-is-transparent
                        # if has_transparency and rewrite_to_jpg:

                        im.save(imgfilename)

                        # Change the tag's width and height attributes, if any
                        if 'width' in tag.attrs:
                            print("Rewriting tag width from %s (%s) to %s"
                                  % (tag.attrs['width'],
                                     type(tag.attrs['width']),
                                     newwidth), file=sys.stderr)
                            tag.attrs['width'] = str(newwidth)
                        if 'height' in tag.attrs:
                            print("Rewriting tag height from %s (%s) to %s"
                                  % (tag.attrs['height'],
                                     type(tag.attrs['height']),
                                     newheight), file=sys.stderr)
                            tag.attrs['height'] = str(newwidth)

                except UnidentifiedImageError:
                    # Image might be SVG or some other non-PIL type
                    print("Can't resize", imgfilename, file=sys.stderr)
                    im = None

        except Exception as e:
            print("Exception", e, "checking maxsize")
            utils.ptraceback()

        # Rewrite the url:
        ImageCache[src] = base
        tag.attrs['src'] = base

    else:
        # Looks like it's probably a nonlocal image, don't download
        print(req.host, "and", host,
              "are too different -- not fetching image", src,
              file=sys.stderr)

        # Add a link to the image, if a BS tag is provided
        if tag:
            # Find tag's root BeautifulSoup object (needed for new_tag)
            soup = tag
            tag.attrs['src'] = "nonlocal"
            while type(soup) is not BeautifulSoup:
                soup = soup.parent
            awrap = soup.new_tag("a", href=src)
            awrap.string = " [nonlocal image]"
            # Originally, added alt text and wrapped the image in it,
            # but Firefox, at least, doesn't show images with
            # src = 'file:///nonexistant' and doesn't even show the
            # alt text for them, so there's nothing visible to click on.
            # tag.wrap(awrap)
            # Instead, just add a tag around a text string.
            tag.append(awrap)
        else:
            print("No soup, can't add image link to", src,
                  file=sys.stderr)

        # We're left with a nonlocal image in the source.
        # That could mean unwanted data use to fetch the image
        # when viewing the file. So remove the image tag and
        # replace it with a link.
        tag.attrs['src'] = alt_src


# The srcset spec is here:
# http://w3c.github.io/html/semantics-embedded-content.html#element-attrdef-img-srcset
# https://html.spec.whatwg.org/multipage/images.html#srcset-attribute
# A simple version is easy:
# srcset="http://site/img1.jpg 1024w,
#         http://site/img1.jpg 150w, ..."
# but some sites, like Wired, embed commas inside their
# image URLs. Since the spaces aren't required, that
# makes the w and the comma the only way to parse it,
# and w and comma are both legal URL characters.
#
# The key is: "If an image candidate string contains no descriptors
# and no ASCII whitespace after the URL, the following image candidate
# string, if there is one, must begin with one or more ASCII whitespace."
# so basically, a comma that's separating image descriptors
# either has to have a space after it, or a w or x before it.
def parse_srcset(srcset_attr):
    '''Parse a SRCSET attribute inside an IMG tag.
       Return a list of pairs [(img_url, descriptor), ...]
       where the descriptor is a resolution ending in w (pixel width)
       or x (pixel density).
    '''
    srcset_attr = srcset_attr.strip()
    if not srcset_attr:
        return []
    commaparts = srcset_attr.split(',')
    parts = []
    for part in commaparts:
        # First, might we be continuing an image URL from the previous part?
        # That's the case if the previous part lacked a descriptor
        # and this part doesn't start with a space.
        if not part.startswith(' ') and parts and not parts[-1][1]:
            part = parts.pop(-1)[0] + ',' + part

        # Does this part have a descriptor?
        match = re.search(' *[0-9]+[wx]$', part)
        if match:
            matched = match.group()
            parts.append((part[0:-len(matched)].strip(), matched.strip()))
        else:
            # Possibly shouldn't strip this, in case there's a
            # continuation (this might only be the first part of
            # an image URL, before a comma) and the URL might have
            # a space in it. But since spaces in URLs are illegal,
            # let's hope for now that no one does that.
            parts.append((part.strip(), None))

    return parts


def same_host(host1, host2):
    """Are two hosts close enough for the purpose of downloading images?"""

    # host can be None:
    if not host1 and not host2:
        return True
    if not host1 or not host2:
        return False

    # For now, a simplistic comparison:
    # are the last two elements (foo.com) the same?
    # Eventually we might want smarter special cases,
    # exceptions for akamai, etc.
    return host1.split('.')[-2:] == host2.split('.')[-2:]


def similar_host(host1, host2, alt_domains):
    """Are two hosts close enough for the purpose of downloading images?
       Or is host1 close to anything in alt_domains?
    """
    if same_host(host1, host2):
        return True
    for d in alt_domains:
        if same_host(host1, d):
            return True
    return False


def make_absolute(url, base_href):
    """Make URLs, particularly img src, absolute according to
       the current page location and any base href we've seen.
    """
    # May want to switch to lxml.html.make_links_absolute(base_href,
    # resolve_base_href=True)

    if not url:
        return url

    if '://' in url:
        return url       # already absolute

    if url.startswith('#'):
        return url

    # If we have a base href then it doesn't matter whether it's
    # relative or absolute.
    if base_href:
        return urllib.parse.urljoin(base_href, url)

    # Map paths without a schema or host to the full URL.
    # Set it here from from the site RSS UR, though that isn't
    # always right, e.g. https://rss.example.com.
    # XXX We should always have a base_href here since it should
    # have been set the first time through fetch_url,
    # so this clause should never trigger. But just in case,
    # leave this clause here for a while.
    if url[0] == '/':
        # if not self.base_href:
        #     print("******** Yikes, got to make_absolute with no base_url",
        #           file=sys.stderr)
        #     url = self.utils.g_config.get(self.feedname, 'url')
        #     urlparts = urllib.parse.urlparse(url)
        #     urlparts = urlparts._replace(path='/')
        #     self.base_href = urllib.parse.urlunparse(urlparts)
        #     print("Set base_href to", self.base_href, file=sys.stderr)

        return urllib.parse.urljoin(base_href, url)

    # It's relative, so append it to the current url minus cur filename:
    return os.path.join(os.path.dirname(cururl), url)

