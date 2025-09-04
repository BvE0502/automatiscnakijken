"""
Streamlit app — Specifiek voor: Excel_Toets_V1_BIG_Antwoordmodel.xlsx
=====================================================================

Deze app is op maat gemaakt voor het meegeleverde **antwoordmodel** met de tabbladen:
- `Instructie`, `Opdrachten`, **`Antwoorden`**, **`Transacties`**, **`Producten`**, **`Categorieën`**.

Wat wordt automatisch nagekeken per studentbestand:
- **O1. VERT.ZOEKEN / toewijzing categorie & productinfo** in `Transacties`:
  - Controleert of kolom **Categorie** overeenkomt met de referentie (op basis van `Producten` ↔ `Categorieën`).
- **O2. Omzet (Aantal × Prijs)** in `Transacties`:
  - Controleert per rij of **Omzet ≈ Aantal × Prijs** en of **totaalomzet** klopt.
- **O3. Omzet incl. 9% BTW voor categorie Groente**:
  - Vergelijkt de som **Omzet(Groente) × 1.09** met de referentie.
- **O4. Draaitabel-achtig resultaat: omzet per categorie**:
  - Vergelijkt **som(Omzet) per Categorie** met de referentie.
- **O12. AANTAL.ALS** (transacties per categorie):
  - Controleert **aantal transacties voor 'Fruit'**.

> **Belangrijk over formules (VERT.ZOEKEN, etc.)**
> We lezen **de laatst opgeslagen waarden** (niet de formule). Laat studenten dus **het bestand openen, herberekenen en opslaan** vóór uploaden.

Installatie (lokaal)
--------------------
1) Maak twee bestanden:
   - `app.py` (dit bestand)
   - `requirements.txt` met:
     ```
     streamlit
     pandas
     openpyxl
     numpy
     xlsxwriter
     ```
2) Start lokaal:
   ```bash
   pip install -r requirements.txt
   streamlit run app.py
   ```

Publiceren op Streamlit Community Cloud
---------------------------------------
1) Zet `app.py` + `requirements.txt` in een **GitHub-repository**.
2) Ga naar https://streamlit.io/cloud → **New app** → selecteer repo/branch → bestandsnaam: `app.py` → **Deploy**.

"""

from __future__ import annotations
import io
from typing import Any, Dict, List, Optional, Tuple

import numpy as np
import pandas as pd
import streamlit as st
from openpyxl import load_workbook

# =============================
# Config
# =============================

st.set_page_config(page_title="Excel Toets – Autonakijken (specifiek)", page_icon="✅", layout="wide")

TOL = 0.01  # numerieke tolerantie bij vergelijken
O1_MIN_MATCH = 0.98  # minimaal % correcte categorie-toewijzingen
O2_MIN_MATCH = 0.98  # minimaal % correcte omzet-rijen
BTW_GROENTE = 0.09   # 9% BTW

# =============================
# Utility: naam-normalisatie en kolomdetectie
# =============================

def norm(s: Any) -> str:
    """Normaliseer een string voor robuuste matching van kolomnamen."""
    if s is None:
        return ""
    s = str(s)
    s = s.strip().lower()
    # verwijder accenten/rare tekens grofweg
    repl = {
        "ä":"a","à":"a","á":"a","â":"a","ã":"a","å":"a",
        "ë":"e","è":"e","é":"e","ê":"e",
        "ï":"i","ì":"i","í":"i","î":"i",
        "ö":"o","ò":"o","ó":"o","ô":"o","õ":"o",
        "ü":"u","ù":"u","ú":"u","û":"u",
        "ñ":"n","ç":"c","ß":"ss","ø":"o",
        "€":"e"
    }
    for k,v in repl.items():
        s = s.replace(k,v)
    # laat alleen alfanumeriek + underscore
    s = "".join(ch if ch.isalnum() else "_" for ch in s)
    while "__" in s:
        s = s.replace("__","_")
    return s.strip("_")

# Synoniemen voor kolommen in Transacties
TX_COLS = {
    "transactieid": {"transactieid", "transactie_id", "id", "transactionid"},
    "datum": {"datum", "date"},
    "productid": {"productid", "product_id"},
    "aantal": {"aantal", "qty", "quantity"},
    "productnaam": {"productnaam", "product", "product_name", "naam"},
    "categoriecode": {"categoriecode", "categorie_code", "catcode", "code"},
    "categorie": {"categorie", "category"},
    "prijs": {"prijs", "price", "unitprice"},
    "omzet": {"omzet", "revenue", "amount", "sales"},
}

# =============================
# Excel inlezen
# =============================

def to_bytesio(uploaded_file) -> io.BytesIO:
    data = uploaded_file.read()
    return io.BytesIO(data)


def read_df(xlsx_bytes: io.BytesIO, sheet: str) -> pd.DataFrame:
    xlsx_bytes.seek(0)
    return pd.read_excel(xlsx_bytes, sheet_name=sheet, engine="openpyxl")


def get_columns(df: pd.DataFrame) -> Dict[str, str]:
    """Map canonieke naam → echte kolomnaam in df, o.b.v. synoniemen."""
    colmap: Dict[str, str] = {}
    inv = {norm(c): c for c in df.columns}
    for canon, syns in TX_COLS.items():
        for s in syns:
            n = norm(s)
            # zoek of deze vorm exact voorkomt in genormaliseerde df-kolommen
            if n in inv:
                colmap[canon] = inv[n]
                break
        if canon not in colmap:
            # tweede kans: zoek substring match in genormaliseerde kolommen
            for nk, orig in inv.items():
                if canon in nk or nk in canon:
                    colmap[canon] = orig
                    break
    return colmap

# =============================
# Referentie opbouwen vanuit Antwoordmodel
# =============================

def build_reference(model_bytes: io.BytesIO) -> Dict[str, Any]:
    """Maak referentiewaarden uit het Antwoordmodel.
    - Som(Omzet) totaal
    - Aantal transacties per categorie (we gebruiken 'Fruit' specifiek voor O12)
    - Som(Omzet) per categorie
    - Som(Omzet) voor 'Groente' incl. 9% BTW
    """
    dfT = read_df(model_bytes, "Transacties")

    # Pak herkenbare kolommen
    colmap = get_columns(dfT)
    c_cat = colmap.get("categorie")
    c_omz = colmap.get("omzet")
    if not c_cat or not c_omz:
        raise ValueError("Model mist kolommen 'Categorie' en/of 'Omzet' in tabblad 'Transacties'.")

    # Totaalomzet
    total_omzet = float(round(dfT[c_omz].sum(), 2))

    # Aantallen per categorie
    counts_by_cat = dfT[c_cat].value_counts(dropna=False).to_dict()

    # Omzet per categorie
    omzet_by_cat = dfT.groupby(c_cat)[c_omz].sum().round(2).to_dict()

    # Omzet incl. BTW voor Groente
    groente_mask = dfT[c_cat].astype(str).str.lower() == "groente"
    groente_omzet_incl = float(round(dfT.loc[groente_mask, c_omz].sum() * (1.0 + BTW_GROENTE), 2))

    return {
        "total_omzet": total_omzet,
        "counts_by_cat": counts_by_cat,
        "omzet_by_cat": omzet_by_cat,
        "groente_omzet_incl": groente_omzet_incl,
    }

# =============================
# Checks op studentbestand
# =============================

def score_student(student_bytes: io.BytesIO, ref: Dict[str, Any]) -> Tuple[pd.DataFrame, float]:
    dfT = read_df(student_bytes, "Transacties")
    colmap = get_columns(dfT)

    # Kolomnamen uit student (sommige kunnen ontbreken)
    c_cat = colmap.get("categorie")
    c_prijs = colmap.get("prijs")
    c_aantal = colmap.get("aantal")
    c_omzet = colmap.get("omzet")
    c_prodid = colmap.get("productid")

    details = []

    # O1: Categorie-toewijzing
    # Voor referentie gebruiken we het Antwoordmodel → omzet_by_cat/ counts_by_cat helpen indirect,
    # maar we willen per-rij check. Daarom reconstrueren we de juiste categorie via 'Producten'+'Categorieën'
    # uit het studentenbestand indien aanwezig, anders uit het modelbestand → daarvoor vragen we model opnieuw aan.

    # Lees (voor O1) product- en categorie-tab uit student; zo niet aanwezig, val terug op model
    try:
        dfP_s = read_df(student_bytes, "Producten")
        dfC_s = read_df(student_bytes, "Categorieën")
        use_student_lookup = True
    except Exception:
        use_student_lookup = False

    # Als student geen lookup-tabbladen heeft, haal we uit model via ref2 (lazy: we bouwen binnen deze functie kortstondig een lookup uit student_bytes → lukt niet → foutmelding vriendelijk)
    if use_student_lookup:
        # Bouw mapping ProductID -> (Prijs, Categorie)
        # Probeer kolommen te vinden
        def get_col(df, candidates):
            inv = {norm(c): c for c in df.columns}
            for cand in candidates:
                if norm(cand) in inv:
                    return inv[norm(cand)]
            return None

        p_pid = get_col(dfP_s, ["ProductID", "product_id"])
        p_name = get_col(dfP_s, ["Product", "productnaam", "naam"])
        p_catcode = get_col(dfP_s, ["CategorieCode", "categorie_code", "code"])
        p_prijs = get_col(dfP_s, ["Prijs", "price"])

        c_code = get_col(dfC_s, ["Code", "categoriecode", "code"])
        c_catname = get_col(dfC_s, ["Categorie", "category"])

        if not (p_pid and p_prijs and p_catcode and c_code and c_catname and c_prodid):
            use_student_lookup = False

    if use_student_lookup:
        dfP_s = dfP_s[[p_pid, p_catcode, p_prijs]].dropna()
        dfC_s = dfC_s[[c_code, c_catname]].dropna()
        dfPC = dfP_s.merge(dfC_s, left_on=p_catcode, right_on=c_code, how="left")
        dfPC = dfPC.rename(columns={p_pid: "ProductID", p_prijs: "Prijs_ref", c_catname: "Categorie_ref"})
        df_check = dfT.merge(dfPC[["ProductID", "Prijs_ref", "Categorie_ref"]], left_on=c_prodid, right_on="ProductID", how="left")
        cat_match = (df_check[c_cat].astype(str).str.strip().str.lower() == df_check["Categorie_ref"].astype(str).str.strip().str.lower()) if c_cat in df_check.columns else pd.Series(False, index=df_check.index)
        cat_match_rate = float(round(cat_match.mean() if len(cat_match) else 0.0, 3))
        o1_ok = (cat_match_rate >= O1_MIN_MATCH)
        details.append({
            "opdracht": "O1 — Categorie toewijzing",
            "resultaat": "GOED" if o1_ok else "FOUT",
            "uitleg": f"Match-rate categorie: {cat_match_rate*100:.1f}% (min {O1_MIN_MATCH*100:.0f}%).",
        })
    else:
        # Kan student-lookup niet bepalen → we keuren O1 af maar met duidelijke hint
        details.append({
            "opdracht": "O1 — Categorie toewijzing",
            "resultaat": "FOUT",
            "uitleg": "Kon tabbladen 'Producten'/'Categorieën' in studentbestand niet betrouwbaar lezen. Controleer VERT.ZOEKEN/X.ZOEKEN en kolommen.",
        })
        o1_ok = False

    # O2: Omzet = Aantal × Prijs en totaalomzet
    if c_aantal and c_prijs and c_omzet:
        # Per-rij vergelijking
        calc = (dfT[c_aantal].astype(float) * dfT[c_prijs].astype(float)).round(2)
        diff = (calc - dfT[c_omzet].astype(float)).abs() <= TOL
        row_rate = float(round(diff.mean(), 3)) if len(diff) else 0.0
        # Totaal
        total_student = float(round(dfT[c_omzet].astype(float).sum(), 2))
        total_ref = float(ref["total_omzet"])  # uit model
        total_ok = abs(total_student - total_ref) <= TOL
        o2_ok = (row_rate >= O2_MIN_MATCH) and total_ok
        details.append({
            "opdracht": "O2 — Omzet (Aantal×Prijs) & totaal",
            "resultaat": "GOED" if o2_ok else "FOUT",
            "uitleg": f"Rij-accuraat: {row_rate*100:.1f}%, totaal: student={total_student:.2f} / ref={total_ref:.2f}.",
        })
    else:
        details.append({
            "opdracht": "O2 — Omzet (Aantal×Prijs) & totaal",
            "resultaat": "FOUT",
            "uitleg": "Ontbrekende kolommen in 'Transacties' (benodigd: Aantal, Prijs, Omzet).",
        })
        o2_ok = False

    # O3: Omzet incl. 9% BTW (alleen Groente)
    if c_cat and c_omzet:
        groente_mask = dfT[c_cat].astype(str).str.lower() == "groente"
        groente_student = float(round(dfT.loc[groente_mask, c_omzet].astype(float).sum() * (1.0 + BTW_GROENTE), 2))
        groente_ref = float(ref["groente_omzet_incl"])
        o3_ok = abs(groente_student - groente_ref) <= TOL
        details.append({
            "opdracht": "O3 — Groente omzet incl. 9% BTW",
            "resultaat": "GOED" if o3_ok else "FOUT",
            "uitleg": f"student={groente_student:.2f} / ref={groente_ref:.2f} (tolerantie ±{TOL}).",
        })
    else:
        details.append({
            "opdracht": "O3 — Groente omzet incl. 9% BTW",
            "resultaat": "FOUT",
            "uitleg": "Benodigd: kolommen 'Categorie' en 'Omzet' in 'Transacties'.",
        })
        o3_ok = False

    # O4: Omzet per categorie (draaitabel)
    if c_cat and c_omzet:
        by_cat_student = dfT.groupby(c_cat)[c_omzet].sum().round(2)
        by_cat_ref = pd.Series(ref["omzet_by_cat"], dtype=float)
        # Reindex zodat beide dezelfde index hebben
        cats = sorted(set(by_cat_student.index.astype(str)) | set(by_cat_ref.index.astype(str)))
        s_stu = by_cat_student.reindex(cats).fillna(0.0)
        s_ref = by_cat_ref.reindex(cats).fillna(0.0)
        diffs = (s_stu - s_ref).abs() <= TOL
        o4_ok = bool(diffs.all())
        # Voor uitleg toon desnoods de grootste afwijking
        max_diff = float((s_stu - s_ref).abs().max()) if len(s_stu) else float("inf")
        details.append({
            "opdracht": "O4 — Omzet per categorie (draaitabel)",
            "resultaat": "GOED" if o4_ok else "FOUT",
            "uitleg": f"Max afwijking per categorie: {max_diff:.2f} (tolerantie ±{TOL}).",
        })
    else:
        details.append({
            "opdracht": "O4 — Omzet per categorie (draaitabel)",
            "resultaat": "FOUT",
            "uitleg": "Benodigd: kolommen 'Categorie' en 'Omzet' in 'Transacties'.",
        })
        o4_ok = False

    # O12: AANTAL.ALS (Fruit)
    if c_cat:
        fruit_count_student = int((dfT[c_cat].astype(str).str.lower() == "fruit").sum())
        fruit_count_ref = int(ref["counts_by_cat"].get("Fruit", 0))
        o12_ok = (fruit_count_student == fruit_count_ref)
        details.append({
            "opdracht": "O12 — AANTAL.ALS Fruit",
            "resultaat": "GOED" if o12_ok else "FOUT",
            "uitleg": f"student={fruit_count_student} / ref={fruit_count_ref}",
        })
    else:
        details.append({
            "opdracht": "O12 — AANTAL.ALS Fruit",
            "resultaat": "FOUT",
            "uitleg": "Benodigd: kolom 'Categorie' in 'Transacties'.",
        })
        o12_ok = False

    # Score
    flags = [x["resultaat"] == "GOED" for x in details]
    pct = round(100.0 * (sum(flags) / len(flags)), 1) if details else 0.0

    return pd.DataFrame(details), pct

# =============================
# Export helpers
# =============================

import xlsxwriter

def df_to_csv_bytes(df: pd.DataFrame) -> bytes:
    return df.to_csv(index=False).encode("utf-8")


def build_feedback_excel(student_name: str, details_df: pd.DataFrame) -> bytes:
    buf = io.BytesIO()
    with pd.ExcelWriter(buf, engine="xlsxwriter") as writer:
        details_df.to_excel(writer, index=False, sheet_name="feedback")
        wb = writer.book
        ws = writer.sheets["feedback"]
        # Conditional formatting op resultaat-kolom
        try:
            res_col = list(details_df.columns).index("resultaat")
            nrows = len(details_df)
            rng = xlsxwriter.utility.xl_range(1, res_col, nrows, res_col)
            fmt_ok = wb.add_format({"bg_color": "#C6EFCE", "font_color": "#006100"})
            fmt_bad = wb.add_format({"bg_color": "#FFC7CE", "font_color": "#9C0006"})
            ws.conditional_format(rng, {"type": "text", "criteria": "containing", "value": "GOED", "format": fmt_ok})
            ws.conditional_format(rng, {"type": "text", "criteria": "containing", "value": "FOUT", "format": fmt_bad})
        except Exception:
            pass
        # Autofit
        for i, col in enumerate(details_df.columns):
            width = max(10, int(details_df[col].astype(str).map(len).max()) + 2)
            ws.set_column(i, i, min(width, 60))
        ws.write(0, 0, f"Feedback voor: {student_name}")
    return buf.getvalue()

# =============================
# UI
# =============================

st.title("✅ Excel-toets autonakijken — specifiek model")

st.markdown(
    """
Upload eerst het **antwoordmodel** (het docentenbestand met ingevulde oplossingen), daarna één of meerdere **studentbestanden**.
De checks zijn specifiek afgestemd op de tabbladen `Transacties`, `Producten`, `Categorieën` zoals in het model.

> **Tip:** Laat studenten het bestand **openen, herberekenen en opslaan** zodat de waarden van formules worden opgeslagen.
    """
)

with st.sidebar:
    st.header("Stap 1 — Upload bestanden")
    model_file = st.file_uploader("Antwoordmodel (xlsx)", type=["xlsx","xlsm"], key="model")
    student_files = st.file_uploader("Studentbestanden (meerdere)", type=["xlsx","xlsm"], accept_multiple_files=True, key="students")
    st.divider()
    st.caption(f"Tolerantie voor numerieke vergelijkingen: ±{TOL}")

if not model_file:
    st.info("⬅️ Upload eerst het antwoordmodel in de sidebar.")
    st.stop()

# Bouw referentie
model_bytes = to_bytesio(model_file)
try:
    ref = build_reference(model_bytes)
except Exception as e:
    st.error(f"Kon referentiewaarden niet opbouwen uit het model: {e}")
    st.stop()

st.subheader("Referentie (uit Antwoordmodel)")
colA, colB, colC = st.columns(3)
with colA:
    st.metric("Totaal omzet", f"€ {ref['total_omzet']:.2f}")
with colB:
    st.metric("Fruit – aantal transacties", ref["counts_by_cat"].get("Fruit", 0))
with colC:
    st.metric("Groente – omzet incl. 9%", f"€ {ref['groente_omzet_incl']:.2f}")

if not student_files:
    st.info("⬅️ Upload nu één of meer studentbestanden in de sidebar.")
    st.stop()

st.subheader("Resultaten per student")
summary = []
zip_parts: List[Tuple[str, bytes]] = []

for up in student_files:
    st.markdown(f"### 👩‍🎓 {up.name}")
    student_bytes = to_bytesio(up)

    try:
        details_df, pct = score_student(student_bytes, ref)
    except Exception as e:
        st.error(f"Kon studentbestand niet beoordelen: {e}")
        continue

    c1, c2 = st.columns([1,2])
    with c1:
        st.metric("% opdrachten goed", f"{pct}%")
    with c2:
        st.dataframe(details_df, use_container_width=True, hide_index=True)

    # downloads
    fb_xlsx = build_feedback_excel(up.name, details_df)
    st.download_button(
        label="📥 Download feedback (Excel)",
        data=fb_xlsx,
        file_name=f"feedback_{up.name.replace('.xlsx','')}.xlsx",
        mime="application/vnd.openxmlformats-officedocument.spreadsheetml.sheet",
        key=f"fb_{up.name}",
    )
    st.download_button(
        label="📥 Download details (CSV)",
        data=df_to_csv_bytes(details_df),
        file_name=f"details_{up.name.replace('.xlsx','')}.csv",
        mime="text/csv",
        key=f"csv_{up.name}",
    )

    summary.append({"student": up.name, "%_goed": pct})
    zip_parts.append((f"feedback_{up.name.replace('.xlsx','')}.xlsx", fb_xlsx))
    zip_parts.append((f"details_{up.name.replace('.xlsx','')}.csv", df_to_csv_bytes(details_df)))

# Overzicht
if summary:
    st.markdown("### 📊 Overzicht")
    sum_df = pd.DataFrame(summary).sort_values(["%_goed","student"], ascending=[False, True])
    st.dataframe(sum_df, use_container_width=True, hide_index=True)

    import zipfile
    def build_zip(parts: List[Tuple[str, bytes]]) -> bytes:
        buf = io.BytesIO()
        with zipfile.ZipFile(buf, mode="w", compression=zipfile.ZIP_DEFLATED) as zf:
            for fname, b in parts:
                zf.writestr(fname, b)
        return buf.getvalue()

    st.download_button(
        label="📦 Download alle feedback als ZIP",
        data=build_zip(zip_parts),
        file_name="alle_feedback.zip",
        mime="application/zip",
        key="zip_all",
    )

with st.expander("ℹ️ Uitleg per check"):
    st.markdown(
        f"""
- **O1 Categorie** — vergelijkt student-**Categorie** per rij met een lookup via `Producten`+`Categorieën` (uit studentbestand). Als die tabbladen ontbreken of kolommen niet herkend worden, valt de check af.
- **O2 Omzet** — controleert per rij of `Omzet ≈ Aantal × Prijs` (tolerantie ±{TOL}) en vergelijkt ook de **totaalomzet** met het model.
- **O3 Groente + BTW** — vergelijkt `som(Omzet) voor Groente × 1.09` met het model.
- **O4 Omzet per categorie** — vergelijkt geaggregeerde `som(Omzet)` per **Categorie** met het model (alle categorieën moeten binnen ±{TOL} liggen).
- **O12 AANTAL.ALS Fruit** — vergelijkt het **aantal** transacties met categorie *Fruit*.

> Let op: we beoordelen de **waarden** (na opslaan), niet of er exact een draaitabel of specifieke formule staat. Dat maakt het nakijken robuust en snel.
        """
    )
