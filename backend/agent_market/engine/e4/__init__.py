"""E4 — web evidence (AM12).

A value that came from a web page is not a value until it can be found
again in the snapshot that was captured of that page. The runner hands this
engine the stored snapshots (never a URL, never the live page) and what a
model said it read in them; the engine goes and looks.

* ``snapshot`` — a captured page as the fetcher stored it: the text a person
  could see, as numbered blocks with the box each is drawn in; where two
  blocks sit in the page relative to each other; a crop of the screenshot.
* ``refind`` — the check. A claim is a quote and the block it was read in.
  The quote must be in that block, character for character, or after a
  mechanical normalisation of spacing and character forms. Nothing about
  what the words mean is decided here.
* ``money`` — what a printed figure is as a number, mechanically: which
  marks are separators and which the decimal point. A figure a model gives
  is accepted only when it is one of the mechanical readings of the string
  it quoted. No amount is ever found by looking for something that reads
  like a price.

The model says where it looked; this code goes and looks there.
"""
