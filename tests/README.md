# Tests

No external test runner needed - both files are plain scripts that exit non-zero
on failure, so they drop straight into CI.

```bash
python tests/test_units.py   # 18 unit checks
python tests/test_e2e.py     # 32 end-to-end checks
```

Neither makes a network call. `test_e2e.py` fakes exactly two boundaries - the
Gemini HTTP transport and the Firestore backend - and runs everything else for
real: Flask routes, auth decorator, prompt construction, Pydantic validation,
normalisers, quote verification, coverage mapping and the repository layer.

The fake Gemini client can be scripted to misbehave (`"bad_json"`, `"empty"`,
`"boom"`) to exercise retry and degradation paths.
