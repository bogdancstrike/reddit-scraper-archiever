"""Phase 2: keyword/phrase filtering + Excel export.

Reads the JSON archive on disk ONLY -- it never re-scrapes. Marks matches via
the flagged/flag_reason/matched_terms fields on the stored records and exports
flagged rows with context to an .xlsx file. Kept as a separate module boundary
so it plugs in without touching the archiver.
"""
