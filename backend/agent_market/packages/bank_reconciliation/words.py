"""What this package says, in the language of the run.

No sentence a person reads on a screen is written in this package's code: it has a key, its Thai is in
agent.json under "strings" and its English under "en" (engine/words.py). What the working paper itself
says is still written in its own code, in Thai — the documents' own pass.
"""

from ...engine import words as _words

W = _words.of(__package__)
