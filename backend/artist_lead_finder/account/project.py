"""The Supabase project of the app.

The anon key is public by design: it only lets a client talk to the project, and the
row-level security rules in supabase/migrations decide what a signed-in user may read or
change. The service role key never goes here.

Empty values turn accounts off: the app then works locally as before (development, tests).
"""

SUPABASE_URL = "https://api.ifeelthisbounce.com"
SUPABASE_ANON_KEY = (
    "eyJhbGciOiJIUzI1NiIsInR5cCI6IkpXVCJ9."
    "eyJyb2xlIjoiYW5vbiIsImlzcyI6InN1cGFiYXNlIiwiaWF0IjoxNzkxMTk3NTI4LCJleHAiOjE5NDg4Nzc1Mjh9."
    "97jsEKTQOD2HzknS_FzPZdjJIIWjcTmKIvPciCFeCz8"
)
# Public half of the server's Ed25519 key that signs offline passes (base64 of 32 bytes,
# printed by supabase/vds/setup.sh). Public by design; empty turns offline work off.
OFFLINE_PUBLIC_KEY = "vEGNFzyilzpDc7vZquIeuO8eihvrrtf3G+9cDp2zZb4="
