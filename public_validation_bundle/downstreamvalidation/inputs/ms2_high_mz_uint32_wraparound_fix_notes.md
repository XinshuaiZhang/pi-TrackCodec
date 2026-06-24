# MS2 high-m/z uint32 wraparound fix notes

The downstream validation identified a strict roundtrip failure in two DDA
files where one high-m/z MS2 point reconstructed lower by `4294.967296 Da`.
That value is exactly:

```text
2^32 / 1e6 = 4294.967296
```

The failure indicated a 32-bit unsigned wraparound in an MS2 m/z path after
quantizing m/z with scale `1e6`.

## Evidence

The affected points had original m/z values above the `uint32` quantized
boundary:

```text
7112.3505859375 * 1e6 = 7,112,350,585.9375
7397.05224609375 * 1e6 = 7,397,052,246.09375
```

If these absolute quantized values are cast to `uint32`, they wrap modulo
`2^32` and decode as `original_mz - 4294.967296`, exactly matching the observed
outlier.

## Fix Principle

The released codec must not store absolute `round(mz * 1e6)` values in
`uint32` unless every value is within the `uint32` range. Safe alternatives are:

- anchor plus checked unsigned offset;
- int64 raw stream;
- signed residual stream with explicit overflow handling.

The public downstream validation bundle includes the fixed-result artifacts and
the figure reproduction code. The diagnostic note is retained here to explain
why the high-m/z regression mattered for downstream validation.

## Regression Expectation

For high-m/z MS2 scans, decoded m/z arrays should satisfy:

```text
max_abs_mz_error <= 5e-7
array_length_mismatch_count == 0
```

No value should reconstruct as `original - 4294.967296`.

