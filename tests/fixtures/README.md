# Synthetic SQLite migration fixtures

`v0.5.sqlite3` contains only fictional test data from the original migration PR.
`v0.7.sqlite3` is a copy upgraded by the real SQLite `Store` at commit `0071d69`;
it adds initial-payment ledger rows and the mobile tables. One fake code hash and
one fake token hash are inserted for fictional sender `22670000001` to verify
that a final snapshot also copies/revokes mobile sessions. No production data,
real tokens or secrets are included.

The v0.5 fixture is kept to check that the migration rejects incomplete older
schemas. The supported cutover source is a full 0.7 snapshot.
