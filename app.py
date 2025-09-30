import streamlit as st
import pandas as pd
import io

st.set_page_config(page_title="LVB Advies Tool", layout="wide")
st.title("📦 LVB Tool met Hermeting Controle")

tab1, tab2, tab3 = st.tabs(["📦 LVB Advies", "📏 Hermeting Controle", "📊 GAP Analyse"])

# -----------------------------------------
# Helpers voor fulfilment (Channeldock + oud)
# -----------------------------------------
def _to_numeric(s):
    return pd.to_numeric(s, errors="coerce").fillna(0)

def load_channeldock_fulfilment(file):
    """
    Leest Channeldock-export (CSV met ';' of XLSX) en mapt naar:
    EAN, Vrije voorraad (Available stock), Verwachte voorraad (In delivery)
    + optioneel extra velden voor toekomstig gebruik.
    """
    name = getattr(file, "name", "")
    try:
        if name.lower().endswith(".csv"):
            df_raw = pd.read_csv(file, sep=";")
        else:
            df_raw = pd.read_excel(file)
    except Exception as e:
        raise ValueError(f"Kan fulfilmentbestand niet lezen: {e}")

    # Kolomnamen case-insensitive benaderen
    lc = {c.lower().strip(): c for c in df_raw.columns}

    # Vereiste Channeldock-kolommen
    col_ean = lc.get("ean")
    col_available = lc.get("available stock")
    col_in_delivery = lc.get("in delivery")

    # Alternatieve benamingen (failsafes)
    if col_available is None:
        for alt in ["available_stock", "available", "free stock", "fc stock"]:
            if alt in lc:
                col_available = lc[alt]
                break
    if col_in_delivery is None:
        for alt in ["in_delivery", "incoming", "incoming qty", "incoming quantity"]:
            if alt in lc:
                col_in_delivery = lc[alt]
                break

    if col_ean is None:
        raise ValueError("Kolom 'EAN' ontbreekt in het Channeldock-bestand.")
    if col_available is None and col_in_delivery is None:
        raise ValueError("Kon geen voorraadkolommen vinden. Verwacht tenminste 'Available stock' of 'In delivery'.")

    out = pd.DataFrame()
    out["EAN"] = df_raw[col_ean].astype(str).str.strip()
    out["Vrije voorraad"] = _to_numeric(df_raw[col_available]) if col_available else 0
    out["Verwachte voorraad"] = _to_numeric(df_raw[col_in_delivery]) if col_in_delivery else 0

    # (Optioneel) Extra velden – nu nog niet gebruikt in advieslogica,
    # maar we nemen ze wel mee als je later wilt tonen:
    # out["FC stock"] = _to_numeric(df_raw[lc.get("fc stock")]) if lc.get("fc stock") else 0
    # out["Total stock (excl. deliveries)"] = _to_numeric(df_raw[lc.get("total stock (excl. deliveries)")]) if lc.get("total stock (excl. deliveries)") else 0

    # Schoon & aggregeer op EAN (soms meerdere regels per EAN)
    out = out.dropna(subset=["EAN"])
    out["EAN"] = out["EAN"].str.split(",").str[0].str.strip()
    out = out.groupby("EAN", as_index=False).agg({
        "Vrije voorraad": "sum",
        "Verwachte voorraad": "sum"
    })
    return out

def try_load_fulfilment(file):
    """
    1) Probeer Channeldock (CSV/XLSX)
    2) Zo niet, val terug op het oude formaat (XLSX) dat al 'Vrije voorraad'/'Verwachte voorraad' heeft.
    Retourneert df met kolommen: EAN, Vrije voorraad, Verwachte voorraad
    """
    # 1) Channeldock parser
    try:
        return load_channeldock_fulfilment(file), "channeldock"
    except Exception as e_cd:
        # 2) Oude formaat fallback (reset pointer indien nodig)
        try:
            file.seek(0)
        except Exception:
            pass
        try:
            if file.name.lower().endswith(".csv"):
                df = pd.read_csv(file)
            else:
                df = pd.read_excel(file)
        except Exception as e_gen:
            raise ValueError(f"Kon fulfilmentbestand niet verwerken.\nChanneldock-fout: {e_cd}\nAlgemene leesfout: {e_gen}")

        needed = {"EAN", "Vrije voorraad", "Verwachte voorraad"}
        if not needed.issubset(set(df.columns)):
            raise ValueError(f"Bestand mist kolommen {needed}. Gevonden kolommen: {list(df.columns)}")

        df = df.copy()
        df["EAN"] = df["EAN"].astype(str).str.strip()
        df["Vrije voorraad"] = _to_numeric(df["Vrije voorraad"])
        df["Verwachte voorraad"] = _to_numeric(df["Verwachte voorraad"])
        return df, "legacy"


# -----------------------------------------
# TAB 1 – LVB Advies
# -----------------------------------------
with tab1:
    wachtwoord = st.text_input("Voer wachtwoord in om verder te gaan:", type="password")
    if wachtwoord != "bhg2k25":
        st.stop()

    st.markdown("### ⏱️ Kies periode voor advies")
    gebruik_14_dagen = st.checkbox("📆 Stuur op basis van 14 dagen (standaard is 28 dagen)")

    if gebruik_14_dagen:
        bol_14_file = st.file_uploader("📥 Upload Bol-export met 14-dagen verkopen (EAN in kolom A, verkopen in kolom I)", type=["xlsx"], key="bol14")
    else:
        bol_14_file = None

    buffer_percentage = st.slider("Instelbare buffer (% van verkopen):", min_value=10, max_value=100, value=30, step=5)

    bol_file = st.file_uploader("📤 Upload Bol-export (.xlsx)", type=["xlsx"])
    fulfilment_file = st.file_uploader("🏬 Upload Fulfilment/Channeldock-export (.csv of .xlsx)", type=["csv", "xlsx"])

    if bol_file and fulfilment_file:
        # Bol inlezen – blijft hetzelfde
        df_bol = pd.read_excel(bol_file)

        # Fulfilment (Channeldock/oud) inlezen
        try:
            df_fulfilment, mode = try_load_fulfilment(fulfilment_file)
            if mode == "channeldock":
                st.info("✅ Channeldock-export herkend en succesvol ingelezen.")
            else:
                st.info("✅ Oud fulfilmentbestand ingelezen (legacy).")
        except Exception as e:
            st.error(f"❌ Fout bij verwerken van het fulfilment-/Channeldock-bestand: {e}")
            st.stop()

        # Eventueel 14-dagen verkoopcijfers overnemen
        if gebruik_14_dagen and bol_14_file:
            try:
                df_14_raw = pd.read_excel(bol_14_file, sheet_name="Gisteren & 14 dagen", dtype=str)
                if df_14_raw.shape[1] <= 8:
                    st.error("❌ Het tabblad 'Gisteren & 14 dagen' bevat < 9 kolommen. Zorg dat kolom A (EAN) en kolom I (verkopen over 14 dagen) aanwezig zijn.")
                    st.stop()

                df_14 = df_14_raw.iloc[:, [0, 8]].copy()
                df_14.columns = ["EAN", "Verkopen_14"]
                df_bol["EAN"] = df_bol["EAN"].astype(str)
                df_14["EAN"] = df_14["EAN"].astype(str)
                df_bol = pd.merge(df_bol, df_14, on="EAN", how="left")
                df_bol["Verkopen (Totaal)"] = df_bol["Verkopen_14"].fillna(0).astype(int)
            except Exception as e:
                st.error("❌ Kan het 14-dagen Excel-bestand niet correct verwerken uit het tabblad 'Gisteren & 14 dagen'. Details: " + str(e))
                st.stop()

        # BOL kolommen normaliseren (zoals bij jou)
        df_bol["EAN"] = df_bol["EAN"].astype(str)
        df_bol["Verkopen (Totaal)"] = pd.to_numeric(df_bol["Verkopen (Totaal)"], errors="coerce").fillna(0).astype(int)
        df_bol["Vrije voorraad"] = pd.to_numeric(df_bol["Vrije voorraad"], errors="coerce").fillna(0)
        # Verzendtype uit kolom 5 (index 4) – dit was in je originele script zo
        df_bol["Verzendtype"] = df_bol.iloc[:, 4].astype(str)

        # Snelle lookup op fulfilment
        fulfil_lookup = df_fulfilment.set_index("EAN")[["Vrije voorraad", "Verwachte voorraad"]].to_dict(orient="index")

        def match_fulfilment(ean):
            row = fulfil_lookup.get(str(ean))
            if row:
                return row.get("Vrije voorraad", 0), row.get("Verwachte voorraad", 0)
            return 0, 0

        resultaten = []
        for _, row in df_bol.iterrows():
            ean = row["EAN"]
            titel = row.get("Titel", "")
            bol_voorraad = row["Vrije voorraad"]
            verkopen = row["Verkopen (Totaal)"]
            verzendtype = row["Verzendtype"]

            fulfilment_vrij, fulfilment_verwacht = match_fulfilment(ean)
            if fulfilment_vrij <= 0 and fulfilment_verwacht <= 0:
                continue

            buffer_grens = verkopen * (buffer_percentage / 100)
            verschil = bol_voorraad - verkopen

            if verschil >= buffer_grens:
                benchmark = "Voldoende"
            elif 0 < verschil < buffer_grens:
                benchmark = "Twijfel"
            else:
                benchmark = "Onvoldoende"

            advies = ""
            aanbevolen = 0
            tekort = max(0, verkopen - bol_voorraad)

            if verzendtype.strip().upper() != "LVB" or benchmark != "Voldoende":
                if benchmark == "Twijfel":
                    if fulfilment_vrij > 0:
                        advies = "Voorraad krap – versturen aanbevolen"
                        aanbevolen = min(fulfilment_vrij, round(tekort * 1.3))
                    elif fulfilment_verwacht > 0:
                        advies = "Nog niet versturen – voorraad verwacht"
                        aanbevolen = 0
                    else:
                        continue
                elif benchmark == "Onvoldoende":
                    if fulfilment_vrij > 0:
                        advies = f"Verstuur minimaal {tekort} stuks"
                        aanbevolen = min(fulfilment_vrij, round(tekort * 1.3))
                    elif fulfilment_verwacht > 0:
                        advies = "Nog niet versturen – voorraad verwacht"
                        aanbevolen = 0
                    else:
                        continue
                elif benchmark == "Voldoende":
                    if verzendtype.strip().upper() != "LVB":
                        if fulfilment_vrij > 0:
                            advies = "Niet op LVB – voorraad beschikbaar – overweeg naar LVB te sturen"
                            aanbevolen = min(fulfilment_vrij, round(verkopen * 1.3))
                        elif fulfilment_verwacht > 0:
                            advies = "Nog niet versturen – voorraad verwacht (niet LVB)"
                            aanbevolen = 0
                        else:
                            continue
                    else:
                        continue

                resultaten.append({
                    "EAN": ean,
                    "Titel": titel,
                    "Benchmarkscore": benchmark,
                    "Verzendtype": verzendtype,
                    "Bol voorraad": bol_voorraad,
                    "Verkopen (Totaal)": verkopen,
                    "Fulfilment vrije voorraad": fulfilment_vrij,
                    "Fulfilment verwachte voorraad": fulfilment_verwacht,
                    "Advies": advies,
                    "Aanbevolen aantal mee te sturen (x1.3 buffer)": aanbevolen
                })

        df_resultaat = pd.DataFrame(resultaten)

        if df_resultaat.empty:
            st.info("ℹ️ Geen producten met (verwachte) fulfilmentvoorraad gevonden die om actie vragen.")
        else:
            benchmark_order = {"Onvoldoende": 0, "Twijfel": 1, "Voldoende": 2}
            df_resultaat["Benchmarkscore_sort"] = df_resultaat["Benchmarkscore"].map(benchmark_order)
            df_resultaat.sort_values(by=["Benchmarkscore_sort", "Verzendtype"], inplace=True)
            df_resultaat.drop(columns=["Benchmarkscore_sort"], inplace=True)

            def kleur_op_benchmark(row):
                if row["Benchmarkscore"] == "Onvoldoende":
                    return ["background-color: #ff3333; color: white"] * len(row)
                elif row["Benchmarkscore"] == "Twijfel":
                    return ["background-color: #ffaa00; color: black"] * len(row)
                elif row["Benchmarkscore"] == "Voldoende":
                    return ["background-color: #33cc33; color: white"] * len(row)
                else:
                    return [""] * len(row)

            st.success("✅ Adviesoverzicht gegenereerd!")
            st.dataframe(df_resultaat.style.apply(kleur_op_benchmark, axis=1), use_container_width=True)

            buffer = io.BytesIO()
            df_resultaat.to_excel(buffer, index=False, engine='openpyxl')
            st.download_button("📥 Download als Excel", data=buffer.getvalue(), file_name="LVB_Advies_Overzicht.xlsx", mime="application/vnd.openxmlformats-officedocument.spreadsheetml.sheet")

            csv = df_resultaat.to_csv(index=False).encode('utf-8')
            st.download_button("📄 Download als CSV", data=csv, file_name="LVB_Advies_Overzicht.csv", mime="text/csv")

# -----------------------------------------
# TAB 2 – Hermeting Controle (ongewijzigd)
# -----------------------------------------
with tab2:
    st.subheader("📏 Hermeting Controle")

    col1, col2 = st.columns(2)
    with col1:
        hermeting_bestand = st.file_uploader("Upload je hermeting sheet (EAN, Naam, Gewenst formaat)", type=["xlsx"], key="hermeting")
    with col2:
        bol_verzendingen = st.file_uploader("Upload Bol verzendexport (EAN, Verzonden formaat)", type=["xlsx"], key="verzending")

    if hermeting_bestand and bol_verzendingen:
        df_hermeting_raw = pd.read_excel(hermeting_bestand, dtype=str)
        df_hermeting = df_hermeting_raw.iloc[:, [0, 1, 2]].copy()
        df_hermeting.columns = ['EAN', 'Productnaam', 'Gewenst formaat']
        df_hermeting['EAN'] = df_hermeting['EAN'].astype(str)

        df_verzonden_raw = pd.read_excel(bol_verzendingen, header=None, dtype=str)
        df_verzonden = pd.DataFrame()
        df_verzonden['EAN'] = df_verzonden_raw.iloc[:, 2]
        df_verzonden['Verzonden formaat'] = df_verzonden_raw.iloc[:, 7]
        df_verzonden.dropna(subset=['EAN'], inplace=True)
        df_verzonden.dropna(subset=['Verzonden formaat'], inplace=True)
        df_verzonden['EAN'] = df_verzonden['EAN'].astype(str)

        df_vergelijk = pd.merge(df_verzonden, df_hermeting, on='EAN', how='left')

        # Verwijder rijen waarbij beide formaten leeg zijn
        df_vergelijk = df_vergelijk.dropna(subset=['Gewenst formaat', 'Verzonden formaat'])

        # Zoek nu echte afwijkingen
        df_afwijkend = df_vergelijk[
            df_vergelijk['Verzonden formaat'].str.lower() != df_vergelijk['Gewenst formaat'].str.lower()
        ]

        df_afwijkend = df_afwijkend.drop_duplicates(subset=['EAN'])

        if not df_afwijkend.empty:
            df_afwijkend['Afwijking'] = "✅ Ja"
            st.success(f"🔎 {len(df_afwijkend)} afwijkende formaten gevonden")
            st.dataframe(df_afwijkend[['EAN', 'Productnaam', 'Gewenst formaat', 'Verzonden formaat', 'Afwijking']], use_container_width=True)

            buffer = io.BytesIO()
            df_afwijkend.to_excel(buffer, index=False, engine='openpyxl')
            st.download_button("📥 Download afwijkingen als Excel", data=buffer.getvalue(), file_name="hermeting_afwijkingen.xlsx", mime="application/vnd.openxmlformats-officedocument.spreadsheetml.sheet")

            csv = df_afwijkend.to_csv(index=False).encode('utf-8')
            st.download_button("📄 Download als CSV", data=csv, file_name="hermeting_afwijkingen.csv", mime="text/csv")
        else:
            st.info("✅ Geen afwijkingen gevonden. Alles komt overeen met je verwachte formaten.")

# -----------------------------------------
# TAB 3 – GAP Analyse (placeholder)
# -----------------------------------------
with tab3:
    st.header("📊 GAP Analyse Tool")
    st.markdown("Voer hieronder de links in van je eigen Bol.com listing en drie concurrenten om een vergelijking te maken.")

    eigen_link = st.text_input("🔗 Link naar jouw product")
    concurrent_links = []
    for i in range(1, 4):
        link = st.text_input(f"🔗 Link naar concurrent {i}")
        concurrent_links.append(link)

    if st.button("📈 Vergelijk Listings"):
        if not eigen_link or any(not link for link in concurrent_links):
            st.warning("Vul alle links in voordat je vergelijkt.")
        else:
            st.success("Links succesvol ontvangen! (De vergelijking volgt in de volgende versie.)")
            st.write("Jouw productlink:", eigen_link)
            for idx, link in enumerate(concurrent_links, start=1):
                st.write(f"Concurrent {idx}:", link)
