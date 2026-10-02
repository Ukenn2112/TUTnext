"""TUTnext backend package."""
import sys

if sys.platform == "emscripten":
    # Cloudflare Python Workers (Pyodide): letting pdfminer be the first importer of
    # `cryptography` aborts the runtime with a Rust panic in the OpenSSL bindings
    # ("panic in a function that cannot unwind"). Importing an asymmetric primitive
    # first initialises the bindings on a path that works, so do it before anything
    # pulls in pdfplumber. See docs/cloudflare-python-workers.md.
    import cryptography.hazmat.primitives.asymmetric.ec  # noqa: F401
