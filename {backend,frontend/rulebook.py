RULEBOOK = {
    "suspicious_method_name": {
        "rule": "RDI method name appears invalid or incorrectly formed.",
        "why": "Incorrect API method names cause runtime or compile-time errors."
    },
    "rdi_block_mismatch": {
        "rule": "RDI_BEGIN and RDI_END must match.",
        "why": "Unmatched blocks cause execution inconsistencies."
    },
    "incomplete_chain": {
        "rule": "RDI method chaining appears incomplete.",
        "why": "Incomplete chaining can break execution logic."
    }
}