# Deployment

Installing and running the pipeline. For making finished results available over HTTP,
see [SERVING.md](SERVING.md).

Three targets. Tier 0 and 1 need no GPU. Tier 2 needs a T4 or better.

## Windows 11, venv, no GPU (Tier 0 and 1)

```
py -3.12 -m venv .venv
.venv\Scripts\activate
pip install -e ".[dev]"
afv info
afv fixtures --out data/fixtures
afv match --config configs/fixtures.yaml && afv score --config configs/fixtures.yaml && afv report --config configs/fixtures.yaml
```

GRIB decoding, try in this order and stop at the first that imports:

1. `pip install cfgrib eccodes` (recent eccodes wheels bundle the C library; verify with `python -c "import cfgrib"`)
2. `pip install earthkit-data`
3. `conda env create -f environment.yml` (conda-forge eccodes)
4. Run `afv fetch` on Kaggle instead and copy `data/cache/fields/*.nc` back; scoring and tests need no GRIB.

Credentials: copy `.env.example` to `.env`, fill `FROST_CLIENT_ID` and `AFV_USER_AGENT`. `.env` is gitignored; `afv` loads it with python-dotenv.

Daily cache while the window is open: `afv fetch --config configs/default.yaml` each evening from the first day of the window. Open data disappears after about four days.

## Kaggle (Tier 0 to 2)

- Notebook settings: Internet on; Accelerator GPU T4 for Tier 2 only.
- Add-ons, Secrets: `FROST_CLIENT_ID`. Read it with `UserSecretsClient().get_secret("FROST_CLIENT_ID")` into `os.environ`.
- Bootstrap cell (notebooks/00): `pip install git+https://github.com/ambrosiobing/aifs-frost-verif ecmwf-opendata cfgrib eccodes verif`.
- Cache under `/kaggle/working/data/cache`; save `results/` as a Kaggle dataset or download the zip.

## Colab

- `google.colab.userdata.get("FROST_CLIENT_ID")`; mount Drive for the cache; T4 free tier for Tier 2.
- Tier 2 extras: `pip install anemoi-inference` and a Flash Attention wheel for sm75 if one exists; otherwise document the failure and stop (see notebooks/05).

## Credential hygiene

- Never print the client id; never paste it into a cell.
- CI greps for a literal `FROST_CLIENT_ID=<value>` in tracked files and fails the build if found.
- Strip notebook outputs before committing (`nbstripout` or clear outputs in the UI).
