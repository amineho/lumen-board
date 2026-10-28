# ASO Tracker 📈

Kaytba3 **Basketball.io: Slam Dunk Hoops** f App Store kol nhar:
- Ma3lomat kamla 3la app (ratings f kol bled, version, size, lughat, description...)
- Rank f **top charts** (All Games + categories dyal app)
- Rank f kol **keyword** o f kol **bled**, kol nhar, b graph
- **Discovery**: kayl9a keywords jdad wa7do, kaytestihom, o ila app ranké f chi wa7d kaysjlo o kayb9a ytba3o
- **Analyse**: kalimat li kaykhdmo bihom concurrents o ma kaynach 3ndek

## Kifach khdam f GitHub

- **GitHub Actions** (`.github/workflows/track.yml`) kaychghel `python tracker.py --run` kol nhar f 08:17 UTC, o kaysjjel data f `aso.db` o `docs/data/`.
- **GitHub Pages** kayhosti dashboard mn dossier `docs/`.
- Mli katbdel `config.json` (mn dashboard wla b yddek), check kaybda wa7do.

### Awel mra

1. **Settings ➜ Pages**: Source = *Deploy from a branch*, Branch = `main`, Folder = `/docs`.
2. 7ell dashboard: `https://<username>.github.io/<repo>/`
3. F dashboard sir l **Settings ➜ GitHub** o 7ett token (steps kaynin tma):
   - Repository access: ghir had repo
   - Permissions: **Contents: Read and write** o **Actions: Read and write**
4. F **Settings** ktb keywords dyalk mn App Store Connect, o zid keywords f tab **Keywords**.

Token kayb9a ghir f browser dyalk (localStorage). Bla token t9der tchouf dashboard, walakin ma t9derch tbdel walo.

### Tbdel b yddek

Kolchi f `config.json`:
- `countries`: l bldan
- `manual_keywords`: `"term"` = f kol l bldan, `"term|us"` = f bled wa7d
- `removed_keywords`: `"term|us"` li ma bghitich ttba3hom
- `my_keywords`: keywords dyalk f App Store Connect
- `candidates_per_run`: ch7al mn keyword jdid ytesta f kol bled f kol nhar

Sa3a dyal check katbdel f `cron` f `.github/workflows/track.yml`.

## Tkhdem bih f PC dyalk (optionnel)

1. Installi Python mn https://www.python.org/downloads/ (f Windows cochi **"Add Python to PATH"**)
2. Double-click 3la `start-windows.bat` (wla `start-mac.command`), wla `python tracker.py`
3. 7ell http://localhost:8080

## Mola7azat

- **Rank**: tool kaychouf tartib l7a9i9i dyal App Store (awel ~10-20 resultats). Apple kat bloki search API mn serveurs d cloud (b7al GitHub). Mn internet dyal dar, ghaliban search API khdama o tool kaychouf 7ta **#200**.
- **"> 12"** ya3ni app machi f awel resultats li tool 9der ychouf.
- Tool kaytsenna 1.5 seconde bin kol request, o ila Apple galt "bzzaf d requests" (429) kaytsenna o kay3awed.
- Kalimat b ⚠️ f Analyse (b7al `nba`) marques dyal nas khrin: Apple katrefd app ila 7tetihom f smiya wla keywords.
