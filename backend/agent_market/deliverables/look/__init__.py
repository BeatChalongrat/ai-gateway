"""The look of every deliverable, in one place (AM21).

``look.json`` beside this file is the one file a person opens to change how every agent's documents,
decks and workbooks look: the type scale (five sizes and their weights, per format), the themes (a
palette, a chart palette in a fixed series order, a cover treatment, a table style) and the layout
catalogue (a small, closed, named set of layouts per format, each declaring what it accepts).

The modules here are the renderer. Nothing outside them may set a font, a size, a colour, a coordinate or
a box — a caller hands over content and its meaning (a figure block, a table, a chart, a note) and the
renderer measures the text in the real typeface and places it in a layout it chose
(``catalogue.choose``). ``tokens.LOOK`` refuses a size or a colour that is not in the scale; the gate
(``agent_market/tests/test_am21_contract.py``) reads the generators' code and fails on geometry outside
this package.
"""
