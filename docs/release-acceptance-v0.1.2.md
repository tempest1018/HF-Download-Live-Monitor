# v0.1.2 release acceptance

Date: 2026-08-27

Result: **accepted for GitHub distribution**. PyPI publication was not performed.

## Release identity and supply chain

- Tag: `v0.1.2`
- Commit: `0496a01beb8531f4a45eae6e05de3d8d45cfbbc9`
- Signing fingerprint: `BF317715C9E7B15A750F481A5C53F25769B6CA89`
- Bundle: 15 exact release files; aggregate `SHA256SUMS` SHA-256
  `27EF769E1AA465D945223B9935BED012522DDBC6590815E749E09273CE92B38E`
- GitHub artifact attestations: eight of eight verified (six native executables,
  wheel, and source distribution).
- The tag is signed by the independently pinned key and descends from protected
  `main`.

## GitHub evidence

- [Repair PR #9](https://github.com/tempest1018/HF-Download-Live-Monitor/pull/9)
- [Post-merge CI](https://github.com/tempest1018/HF-Download-Live-Monitor/actions/runs/33079503699)
- [Release build and staging](https://github.com/tempest1018/HF-Download-Live-Monitor/actions/runs/33080223417)
- [Protected GitHub publication](https://github.com/tempest1018/HF-Download-Live-Monitor/actions/runs/33080567393)
- [Published-release acceptance](https://github.com/tempest1018/HF-Download-Live-Monitor/actions/runs/33080641044)

The published-release workflow passed the isolated public wheel and all six native
targets: Windows x86_64/ARM64, Linux x86_64/ARM64, and macOS x86_64/ARM64.

## Independent local consumer evidence

All tests used assets freshly downloaded from the public GitHub release, outside the
source checkout, against a deterministic localhost Hugging Face-compatible endpoint.

| Consumer | Repetitions | Result | Redacted report SHA-256 |
| --- | ---: | --- | --- |
| Windows x86_64 standalone | 1 | Passed | `0D7EB67879C71A96CFADB0595A314D9D59B86D081C95599C50CF1A51EA562B84` |
| Public wheel in clean venv | 1 | Passed | `85BEE685FD5BD9FB4C3BF4BB4627B0E7738B322D20F500E6E95CEA74F69BB130` |
| Linux x86_64 standalone in Python 3.13 Docker | 4 | Passed 4/4 | `6FAFAAB9D4EA6DC4299D240ECEEFE548862AF87B1D3029B5BCFA263A832700C0` |

The clean-wheel check also proved the imported package came from the temporary virtual
environment's `site-packages`, not the repository. A PTY run of the public Windows
standalone displayed the adaptive dashboard and finished at 100%, with one verified
file, zero complete-unverified files, and zero failed files.

## Defect found and corrected during acceptance

The same Docker consumer rejected v0.1.1 because the frozen Linux executable passed
PyInstaller's bundled `LD_LIBRARY_PATH` to the external `hf` process. Python 3.13 then
loaded an incompatible bundled OpenSSL library and the monitor correctly reported an
incomplete file. v0.1.2 restores the host library path before starting external child
processes; a focused regression test and four consecutive Docker acceptance runs cover
the correction.

The v0.1.0 and v0.1.1 tags and assets remain immutable historical releases. v0.1.2 is
the accepted corrective release.
