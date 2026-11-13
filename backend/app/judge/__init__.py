"""The AI judge: what it is asked, what it must return, and the model that answers.

The judge never grades alone. ``app.engine.grading`` looks for every quote it cites in the
utterance it names, verifies each coverage claim it finds against the event log, and combines
its rulings with the deterministic checks.
"""
