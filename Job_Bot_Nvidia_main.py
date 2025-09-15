# -*- coding: utf-8 -*-

"""
Bot NVIDIA Workday — Parties 1, 2, 3 & 4 (+ Apply Flow)

* Partie 1 : Login (avec iframe) — inchangée
* Partie 2 : Filtre Location=Israel → View Jobs → récupérer les 20 premières offres — inchangée
* Partie 3 : Pour chaque offre (dans un seul onglet) :
    - extraire la description, générer un texte EN, sauvegarder
    - tenter la candidature: Apply → Use My Last Application → How did you hear... → Website → NVIDIA.COM
      → Save & Continue → Gender=Male → cocher T&C → Save & Continue → Submit (si dispo)
    - fermer l’onglet et revenir aux résultats
* Partie 4 : Orchestration (ne ferme pas le navigateur) — inchangée
* NOUVEAU : Pagination — après avoir traité la page courante, le bot va sur la page suivante (flèche '>') et recommence.
             S’il n’y a plus de page suivante, il s’arrête (sans fermer la fenêtre).
"""

from __future__ import annotations
import sys
import os
import re
import time
from dataclasses import dataclass
from typing import Tuple, List, Dict, Optional
from urllib.parse import urljoin

from selenium import webdriver
from selenium.webdriver.common.by import By
from selenium.webdriver.chrome.service import Service
from webdriver_manager.chrome import ChromeDriverManager
from selenium.webdriver.support.ui import WebDriverWait
from selenium.webdriver.support import expected_conditions as EC
from selenium.common.exceptions import TimeoutException, NoSuchElementException
from selenium.webdriver.common.action_chains import ActionChains
from selenium.webdriver.common.keys import Keys

# =========================
# CONFIG
# =========================

LOGIN_URL = "https://nvidia.wd5.myworkdayjobs.com/en-US/NVIDIAExternalCareerSite/login"
ROOT_URL  = "https://nvidia.wd5.myworkdayjobs.com/en-US/NVIDIAExternalCareerSite"


# (Optionnel) dossier avec des fichiers texte de ton profil (CV, lettres, etc.)
PROFILE_DOCS_DIR: Optional[str] = None  # ex: r"C:\Users\ouakn\Documents\Profile_Texts"

@dataclass
class SeleniumConfig:
    headless: bool = False
    default_wait_s: int = 20
    page_load_timeout_s: int = 60
    pause_on_error: bool = True  # met le bot en pause (input) si erreur inattendue

@dataclass
class Credentials:
    email: str
    password: str

# =========================
# OUTILS
# =========================

def build_driver(cfg: SeleniumConfig) -> webdriver.Chrome:
    from selenium.webdriver.chrome.options import Options
    options = Options()
    if cfg.headless:
        options.add_argument("--headless=new")
    options.add_argument("--start-maximized")
    options.add_argument("--window-size=1400,900")
    options.add_argument("--disable-gpu")
    options.add_argument("--no-sandbox")
    # Important : laisser la fenêtre ouverte après la fin du script
    options.add_experimental_option("detach", True)

    driver = webdriver.Chrome(service=Service(ChromeDriverManager().install()), options=options)
    driver.set_page_load_timeout(cfg.page_load_timeout_s)
    return driver

def switch_to_frame_containing(driver: webdriver.Chrome, locator: Tuple[str, str], timeout: int = 15) -> None:
    """Bascule dans l’(iframe) qui contient `locator` (jusqu’à 2 niveaux)."""
    driver.switch_to.default_content()
    wait = WebDriverWait(driver, timeout)

    try:
        wait.until(EC.presence_of_element_located(locator))
        return
    except TimeoutException:
        pass

    for f1 in driver.find_elements(By.TAG_NAME, "iframe"):
        driver.switch_to.default_content()
        driver.switch_to.frame(f1)
        try:
            WebDriverWait(driver, timeout).until(EC.presence_of_element_located(locator))
            return
        except TimeoutException:
            for f2 in driver.find_elements(By.TAG_NAME, "iframe"):
                driver.switch_to.default_content()
                driver.switch_to.frame(f1)
                driver.switch_to.frame(f2)
                try:
                    WebDriverWait(driver, timeout).until(EC.presence_of_element_located(locator))
                    return
                except TimeoutException:
                    continue

    driver.switch_to.default_content()
    raise NoSuchElementException(f"Élément introuvable (même via iframes) : {locator}")

def robust_click(driver: webdriver.Chrome, element) -> bool:
    """Clic résilient : direct -> scroll+JS -> ActionChains."""
    try:
        element.click()
        return True
    except Exception:
        pass
    try:
        driver.execute_script("arguments[0].scrollIntoView({block:'center'});", element)
        driver.execute_script("arguments[0].click();", element)
        return True
    except Exception:
        pass
    try:
        ActionChains(driver).move_to_element(element).pause(0.1).click().perform()
        return True
    except Exception:
        return False

def safe_click(driver, locator, timeout=10):
    try:
        el = WebDriverWait(driver, timeout).until(EC.element_to_be_clickable(locator))
        return robust_click(driver, el)
    except Exception:
        return False

def text_or_empty(el) -> str:
    try:
        return el.text.strip()
    except Exception:
        return ""

def clean_slug(s: str) -> str:
    s = s.strip().lower()
    s = re.sub(r"[^\w-]+", "-", s)
    s = re.sub(r"-{2,}", "-", s).strip("-")
    return s or "job"

def ensure_dir(path: str) -> None:
    os.makedirs(path, exist_ok=True)

def unique_path(base_dir: str, filename: str) -> str:
    """
    Retourne un chemin unique : si 'filename' existe, ajoute _2, *3, ...
    """
    root, ext = os.path.splitext(filename)
    candidate = os.path.join(base_dir, filename)
    i = 2
    while os.path.exists(candidate):
        candidate = os.path.join(base_dir, f"{root}*{i}{ext}")
        i += 1
    return candidate

def read_profile_corpus(profile_dir: Optional[str]) -> str:
    if not profile_dir or not os.path.isdir(profile_dir):
        return ""
    texts = []
    for fn in os.listdir(profile_dir):
        if fn.lower().endswith((".txt", ".md")):
            try:
                with open(os.path.join(profile_dir, fn), "r", encoding="utf-8") as f:
                    texts.append(f.read().strip())
            except Exception:
                continue
    return "\n\n".join(texts).strip()

def debug_pause(cfg: SeleniumConfig, msg: str):
    print(f"\n⚠️  {msg}\n")
    if cfg.pause_on_error:
        try:
            input("⏸️  Bot en pause. Appuie sur Entrée pour continuer…")
        except EOFError:
            # Au cas où l’environnement n’autorise pas input()
            time.sleep(5)

def wait_short():
    time.sleep(0.8)

# =========================
# PARTIE 1 — LOGIN
# =========================

def part1_login(driver: webdriver.Chrome, creds: Credentials, cfg: SeleniumConfig) -> None:
    """
    - Ouvre la page Login
    - Va dans l’iframe
    - Remplit email/password (data-automation-id)
    - Clique le DIV “Sign In” (pas le bouton masqué)
    """
    wait = WebDriverWait(driver, cfg.default_wait_s)
    driver.get(LOGIN_URL)

    # (Optionnel) bandeau cookies
    try:
        el = WebDriverWait(driver, 5).until(
            EC.element_to_be_clickable((By.XPATH, "//button[contains(., 'Accept') or contains(., 'Accepter') or contains(., 'OK')]"))
        )
        el.click()
    except TimeoutException:
        pass

    # Locators champs
    email_loc = (By.CSS_SELECTOR, 'input[data-automation-id="email"]')
    pwd_loc   = (By.CSS_SELECTOR, 'input[data-automation-id="password"]')

    # Viser le DIV Sign In visible (évite <button aria-hidden="true">)
    sign_in_locators = [
        (By.CSS_SELECTOR, 'div[role="button"][aria-label="Sign In"]'),
        (By.XPATH, '//div[@role="button" and @aria-label="Sign In"]'),
        (By.CSS_SELECTOR, 'div[data-automation-id="click_filter"][role="button"][aria-label="Sign In"]'),
        (By.XPATH, "//*[(@role='button') and (@aria-label='Sign In') and not(@aria-hidden='true')]"),
    ]

    # Rentrer dans l’iframe puis saisir
    switch_to_frame_containing(driver, email_loc, timeout=cfg.default_wait_s)

    email_el = wait.until(EC.presence_of_element_located(email_loc))
    email_el.clear(); email_el.send_keys(creds.email)

    pwd_el = wait.until(EC.presence_of_element_located(pwd_loc))
    pwd_el.clear(); pwd_el.send_keys(creds.password)

    # ENTER (souvent suffisant) + fallback clic robuste
    try:
        pwd_el.send_keys(Keys.ENTER)
    except Exception:
        pass

    sign_el = None
    for loc in sign_in_locators:
        try:
            el = WebDriverWait(driver, 8).until(EC.presence_of_element_located(loc))
            if el.is_displayed():
                sign_el = el
                break
        except TimeoutException:
            continue

    if not sign_el:
        raise NoSuchElementException("DIV 'Sign In' introuvable dans l’iframe.")

    if not robust_click(driver, sign_el):
        try:
            ActionChains(driver).move_to_element(sign_el).send_keys(Keys.SPACE).perform()
        except Exception:
            raise RuntimeError("Échec du clic sur 'Sign In' (DIV).")

    # Attendre un changement (parfois l’URL ne change pas)
    try:
        wait.until(EC.url_changes(LOGIN_URL))
    except TimeoutException:
        pass

    driver.switch_to.default_content()
    driver.get(ROOT_URL)

# =========================
# PARTIE 2 — Filtrer Israel → "View Jobs" → extraire 20 premières offres
# =========================

def part2_select_israel_and_collect_20(driver: webdriver.Chrome, cfg: SeleniumConfig) -> List[Dict]:
    """
    - Ouvre le filtre Location
    - Coche 'Israel' via label for='2fcb99c455831013ea52bbe14cf9326c'
    - Clique sur "View Jobs"
    - Récupère les 20 premières offres (titre + href absolu)
    """
    wait = WebDriverWait(driver, cfg.default_wait_s)
    driver.switch_to.default_content()

    if not driver.current_url.startswith(ROOT_URL):
        driver.get(ROOT_URL)

    # 1) Ouvrir le menu Location
    loc_button_candidates = [
        (By.CSS_SELECTOR, 'button[data-automation-id="distanceLocation"]'),
        (By.XPATH, '//button[@data-automation-id="distanceLocation"]'),
        (By.XPATH, "//button[contains(., 'Location')]"),
    ]
    opened = any(safe_click(driver, loc, timeout=8) for loc in loc_button_candidates)
    if not opened:
        raise NoSuchElementException("Bouton Location introuvable.")

    # 2) Cocher 'Israel'
    israel_label_locators = [
        (By.XPATH, "//label[@for='2fcb99c455831013ea52bbe14cf9326c']"),
        (By.XPATH, "//label[contains(normalize-space(.), 'Israel')]"),
    ]
    israel_label = None
    for loc in israel_label_locators:
        try:
            el = wait.until(EC.presence_of_element_located(loc))
            items = driver.find_elements(*loc)
            israel_label = next((i for i in items if i.is_displayed()), el)
            break
        except TimeoutException:
            continue
    if not israel_label:
        raise NoSuchElementException("Label 'Israel' introuvable dans le filtre Location.")

    if not robust_click(driver, israel_label):
        try:
            cb = driver.find_element(By.ID, "2fcb99c455831013ea52bbe14cf9326c")
            driver.execute_script("arguments[0].click();", cb)
        except Exception:
            raise RuntimeError("Impossible de cocher 'Israel'.")

    # 3) Cliquer "View Jobs" (prioritaire)
    ok = safe_click(driver, (By.CSS_SELECTOR, "button[data-automation-id='viewAllJobsButton']"), timeout=6)
    if not ok:
        # Fallback si un tenant affiche Apply/Done
        apply_candidates = [
            (By.CSS_SELECTOR, "button[data-automation-id='filterDialogApplyButton']"),
            (By.XPATH, "//button[contains(., 'Apply') and not(contains(., 'Now'))]"),
            (By.XPATH, "//button[contains(., 'Done')]"),
            (By.XPATH, "//button[contains(., 'OK')]"),
        ]
        for loc in apply_candidates:
            if safe_click(driver, loc, timeout=3):
                break

    # 4) Attendre la page des jobs
    wait.until(EC.presence_of_all_elements_located((By.CSS_SELECTOR, 'a[data-automation-id="jobTitle"]')))
    time.sleep(2)

    # 5) Récupérer 20 premières offres
    links = [a for a in driver.find_elements(By.CSS_SELECTOR, 'a[data-automation-id="jobTitle"]') if a.is_displayed()]
    jobs: List[Dict] = []
    for a in links[:20]:
        title = text_or_empty(a)
        href = a.get_attribute("href") or ""
        if href.startswith("/"):
            href = urljoin(ROOT_URL, href)
        jobs.append({"title": title, "url": href})

    # Affichage console
    print("\n=== Top 20 jobs (Israel) ===")
    for i, j in enumerate(jobs, 1):
        print(f"{i:02d}. {j['title']}  ->  {j['url']}")
    print("===========================\n")

    return jobs

# =========================
# PARTIE 3 — Extraction + Rédaction + Sauvegarde
# =========================

def extract_job_page_details(driver: webdriver.Chrome, cfg: SeleniumConfig) -> Dict[str, str]:
    """
    Sur une page détail d’offre :
    - Titre
    - Localisation
    - Requisition ID
    - Description (bloc data-automation-id="jobPostingDescription")
    """
    wait = WebDriverWait(driver, cfg.default_wait_s)

    # Titre
    title = ""
    title_candidates = [
        (By.CSS_SELECTOR, 'h1'),
        (By.CSS_SELECTOR, '[data-automation-id="jobPostingHeader"] h1'),
        (By.XPATH, "//h1"),
    ]
    for loc in title_candidates:
        try:
            el = wait.until(EC.presence_of_element_located(loc))
            if el and el.is_displayed():
                title = text_or_empty(el)
                if title:
                    break
        except TimeoutException:
            continue

    # Localisation (facultatif)
    location = ""
    try:
        loc_el = driver.find_element(By.CSS_SELECTOR, '[data-automation-id="locations"] dd')
        location = text_or_empty(loc_el)
    except Exception:
        pass

    # Requisition ID (facultatif)
    req_id = ""
    try:
        rid = driver.find_element(By.CSS_SELECTOR, '[data-automation-id="requisitionId"] dd')
        req_id = text_or_empty(rid)
    except Exception:
        m = re.search(r"(JR\d{6,})", driver.page_source)
        if m:
            req_id = m.group(1)

    # Description
    description = ""
    desc_candidates = [
        (By.CSS_SELECTOR, '[data-automation-id="jobPostingDescription"]'),
        (By.XPATH, '//*[@data-automation-id="jobPostingDescription"]'),
    ]
    for loc in desc_candidates:
        try:
            el = wait.until(EC.presence_of_element_located(loc))
            if el and el.is_displayed():
                description = el.text.strip()
                if description:
                    break
        except TimeoutException:
            continue

    return {
        "title": title,
        "location": location,
        "req_id": req_id,
        "description": description,
    }


# =========================
# *** NOUVEAU *** — Flux Apply (étapes 1→12 + Submit)
# =========================

def apply_flow_for_current_job(driver: webdriver.Chrome, cfg: SeleniumConfig) -> bool:
    """
    Exécute le flux Apply → Use My Last Application → How did you hear... → Website → NVIDIA.COM
    → Save & Continue → Gender=Male → T&C → Save & Continue → Submit (si présent).
    Retourne True si on a effectivement tenté/soumis une candidature, False sinon (ex: View Application visible).
    """
    wait = WebDriverWait(driver, cfg.default_wait_s)

    # 0) Si "View Application" est là, on passe à l'offre suivante
    try:
        view_btns = driver.find_elements(By.CSS_SELECTOR, 'button[data-automation-id="viewButton"]')
        if any(b.is_displayed() for b in view_btns):
            print("ℹ️  'View Application' détecté — offre déjà traitée. On passe à la suivante.")
            return False
    except Exception:
        pass

    # 1) Bouton Apply
    print("➡️  Étape 1: clic Apply…")
    apply_locators = [
        (By.CSS_SELECTOR, "a[data-uxi-element-id='Apply_adventureButton']"),
        (By.CSS_SELECTOR, "a[data-automation-id='adventureButton']"),
        (By.XPATH, "//a[@role='button' and normalize-space()='Apply']"),
        (By.XPATH, "//a[contains(., 'Apply')]"),
    ]
    clicked = any(safe_click(driver, loc, timeout=8) for loc in apply_locators)
    if not clicked:
        debug_pause(cfg, "Bouton 'Apply' introuvable.")
        return False
    wait_short()

    # 2) "Use My Last Application"
    print("➡️  Étape 2: Use My Last Application…")
    use_last_locs = [
        (By.CSS_SELECTOR, "a[data-automation-id='useMyLastApplication']"),
        (By.XPATH, "//a[contains(@href,'useMyLastApplication')]"),
        (By.XPATH, "//a[normalize-space()='Use My Last Application']"),
    ]
    ok = any(safe_click(driver, loc, timeout=12) for loc in use_last_locs)
    if not ok:
        # Si on tombe directement sur le form (selon tenant), on continue
        print("ℹ️  Lien 'Use My Last Application' non trouvé — on continue si le formulaire est ouvert.")
    wait_short()

    # 3) Ouvrir le menu "How did you hear about us" (icône 3 lignes)
    print("➡️  Étape 3: ouvrir 'How did you hear about us'…")
    opened = False
    try:
        # cibler l’icône dans la section qui contient le titre
        icon = WebDriverWait(driver, 10).until(EC.presence_of_element_located((
            By.XPATH,
            "//section[.//h2[contains(translate(., 'ABCDEFGHIJKLMNOPQRSTUVWXYZ', 'abcdefghijklmnopqrstuvwxyz'), 'how did you')]]"
            "//svg[contains(@class,'wd-icon-prompts')]"
        )))
        if icon.is_displayed():
            opened = robust_click(driver, icon)
    except TimeoutException:
        # fallback: n'importe quelle icône wd-icon-prompts visible
        for ic in driver.find_elements(By.CSS_SELECTOR, "svg.wd-icon-prompts"):
            if ic.is_displayed():
                opened = robust_click(driver, ic)
                if opened:
                    break
    if not opened:
        debug_pause(cfg, "Impossible d’ouvrir le menu 'How did you hear about us'.")
        return False
    wait_short()

    # 4) Choisir "Website"
    print("➡️  Étape 4: choisir 'Website'…")
    website_locs = [
        (By.CSS_SELECTOR, "div[data-automation-id='promptOption'][data-automation-label='Website']"),
        (By.XPATH, "//div[@data-automation-id='promptOption' and @data-automation-label='Website']"),
        (By.XPATH, "//div[normalize-space()='Website' and @data-automation-id='promptOption']"),
    ]
    if not any(safe_click(driver, loc, timeout=10) for loc in website_locs):
        debug_pause(cfg, "Option 'Website' introuvable.")
        return False
    wait_short()

    # 5) Cocher NVIDIA.COM (ou radio associé) puis valider
    print("➡️  Étape 5: cocher 'NVIDIA.COM'…")
    nvidia_option_locs = [
        (By.CSS_SELECTOR, "div[data-automation-id='promptOption'][data-automation-label='NVIDIA.COM']"),
        (By.XPATH, "//div[@data-automation-id='promptOption' and @data-automation-label='NVIDIA.COM']"),
        (By.XPATH, "//div[normalize-space()='NVIDIA.COM' and @data-automation-id='promptOption']"),
    ]
    clicked_nv = any(safe_click(driver, loc, timeout=6) for loc in nvidia_option_locs)
    if not clicked_nv:
        # fallback: bouton radio voisin
        radios = driver.find_elements(By.CSS_SELECTOR, "input[data-automation-id='radioBtn']")
        for r in radios:
            try:
                if r.is_displayed():
                    driver.execute_script("arguments[0].click();", r)
                    clicked_nv = True
                    break
            except Exception:
                continue
    if not clicked_nv:
        debug_pause(cfg, "Choix 'NVIDIA.COM' introuvable.")
        return False

    # 6) Save and Continue (dialogue)
    print("➡️  Étape 6: Save and Continue…")
    next_locs = [
        (By.CSS_SELECTOR, "button[data-automation-id='pageFooterNextButton']"),
        (By.XPATH, "//button[contains(., 'Save and Continue')]"),
    ]
    if not any(safe_click(driver, loc, timeout=10) for loc in next_locs):
        debug_pause(cfg, "Bouton 'Save and Continue' (dialogue) introuvable.")
        return False
    wait_short()

    # 7) Save and Continue (en bas de page)
    print("➡️  Étape 7: Save and Continue (bas de page)…")
    any(safe_click(driver, loc, timeout=12) for loc in next_locs)
    wait_short()

    # 8) Ouvrir le sélecteur Gender (Select One)
    print("➡️  Étape 8–9: Gender → Male…")
    gender_btn_locs = [
        (By.ID, "personalInfoPerson--gender"),
        (By.CSS_SELECTOR, "button#personalInfoPerson--gender"),
        (By.XPATH, "//button[@name='gender' or @id='personalInfoPerson--gender']"),
        (By.XPATH, "//button[contains(@aria-label,'Select One')]"),
    ]
    opened_gender = any(safe_click(driver, loc, timeout=10) for loc in gender_btn_locs)
    if opened_gender:
        male_option_locs = [
            (By.XPATH, "//li[@role='option' and normalize-space()='Male']"),
            (By.CSS_SELECTOR, "li.css-1fjyfvd div"),  # fallback très large
        ]
        # essayer d'abord l’option explicite
        if not any(safe_click(driver, loc, timeout=6) for loc in male_option_locs[:1]):
            # fallback: cliquer un des <li> avec 'Male'
            found = False
            for li in driver.find_elements(By.XPATH, "//li[@role='option']"):
                if 'male' in text_or_empty(li).lower():
                    found = robust_click(driver, li)
                    break
            if not found:
                debug_pause(cfg, "Option 'Male' introuvable dans la liste.")
                return False
    else:
        debug_pause(cfg, "Bouton 'Gender / Select One' introuvable.")
        return False
    wait_short()

    # 10) Cocher T&C — (corrigé précédemment)
    print("➡️  Étape 10: cocher T&C…")

    # 10.a — S'assurer que la section est rendue (Workday virtualise hors écran)
    try:
        header = driver.find_element(
            By.XPATH,
            "//h3[@id='Terms-and-Conditions-section' or normalize-space()='Terms and Conditions']"
        )
        driver.execute_script("arguments[0].scrollIntoView({block:'center'});", header)
        time.sleep(0.6)
    except Exception:
        # Fallback : forcer un scroll bas pour déclencher le rendu, puis remonter un peu
        driver.execute_script("window.scrollTo(0, document.body.scrollHeight);")
        time.sleep(0.8)
        driver.execute_script("window.scrollBy(0, -300);")
        time.sleep(0.4)

    checked = False

    # 10.b — Essayer d'abord le LABEL
    try:
        label = WebDriverWait(driver, 12).until(EC.element_to_be_clickable(
            (By.XPATH, "//label[@for='termsAndConditions--acceptTermsAndAgreements']")
        ))
        driver.execute_script("arguments[0].scrollIntoView({block:'center'});", label)
        if robust_click(driver, label):
            checked = True
    except TimeoutException:
        pass

    # 10.c — Fallback : INPUT / SPAN stylé
    if not checked:
        tc_try_locs = [
            (By.ID, "termsAndConditions--acceptTermsAndAgreements"),
            (By.XPATH, "//input[@type='checkbox' and @name='acceptTermsAndAgreements']"),
            (By.XPATH, "//input[@id='termsAndConditions--acceptTermsAndAgreements']/following-sibling::span"),
            (By.XPATH, "//div[@data-automation-id='formField-acceptTermsAndAgreements']//span[contains(@class,'css-')]"),
        ]
        for loc in tc_try_locs:
            try:
                el = WebDriverWait(driver, 8).until(EC.presence_of_element_located(loc))
                driver.execute_script("arguments[0].scrollIntoView({block:'center'});", el)
                if robust_click(driver, el):
                    checked = True
                    break
            except TimeoutException:
                continue

    # 10.d — Ultime fallback : JS + change
    if not checked:
        try:
            cb = driver.find_element(By.ID, "termsAndConditions--acceptTermsAndAgreements")
            driver.execute_script(
                "arguments[0].checked = true;"
                "arguments[0].setAttribute('aria-checked','true');"
                "arguments[0].dispatchEvent(new Event('change', {bubbles:true}));",
                cb
            )
            checked = True
        except Exception:
            pass

    # 10.e — Vérification
    try:
        cb = driver.find_element(By.ID, "termsAndConditions--acceptTermsAndAgreements")
        state = cb.get_attribute("aria-checked")
        is_selected = cb.is_selected()
        if (state not in ("true", True)) and (not is_selected):
            raise Exception("T&C non coché")
    except Exception:
        debug_pause(cfg, "Case 'Terms and Conditions' introuvable ou non cliquable.")
        return False

    wait_short()

    # 11) Save and Continue
    print("➡️  Étape 11: Save and Continue…")
    if not any(safe_click(driver, loc, timeout=12) for loc in next_locs):
        debug_pause(cfg, "Bouton 'Save and Continue' (fin de page) introuvable.")
        return False
    wait_short()

    # 12) Review → Submit (scroll bas + clic + attente de confirmation/disparition)
    print("➡️  Étape 12: Review → Submit…")
    try:
        # Attendre la section Review (si elle existe)
        try:
            review_h2 = WebDriverWait(driver, 12).until(
                EC.presence_of_element_located((By.XPATH, "//h2[normalize-space()='Review']"))
            )
            # se positionner dessus puis en bas de page (footer collant)
            driver.execute_script("arguments[0].scrollIntoView({block:'center'});", review_h2)
            time.sleep(0.4)
        except TimeoutException:
            # Pas bloquant : certains tenants sautent l'en-tête
            pass

        # Scroll bas pour assurer le rendu du footer
        driver.execute_script("window.scrollTo(0, document.body.scrollHeight);")
        time.sleep(0.6)

        # Localiser le bouton Submit dans le footer Workday
        submit_loc = (By.CSS_SELECTOR, "div[data-automation-id='pageFooter'] button[data-automation-id='pageFooterNextButton']")
        submit_btn = WebDriverWait(driver, 15).until(EC.element_to_be_clickable(submit_loc))
        driver.execute_script("arguments[0].scrollIntoView({block:'center'});", submit_btn)
        time.sleep(0.2)
        clicked = robust_click(driver, submit_btn)
        if clicked:
            print("✅ Submit cliqué, attente de confirmation…")
        else:
            # dernier recours : JS click
            try:
                driver.execute_script("arguments[0].click();", submit_btn)
                print("✅ Submit cliqué via JS, attente de confirmation…")
            except Exception:
                debug_pause(cfg, "Impossible de cliquer sur 'Submit'.")
                return True  # on ne bloque pas tout le flux

        # Attendre jusqu'à 20 s : bouton disparu/désactivé ou section Review absente
        start = time.time()
        while time.time() - start < 20:
            still_review = False
            try:
                el = driver.find_element(By.XPATH, "//h2[normalize-space()='Review']")
                still_review = el.is_displayed()
            except Exception:
                still_review = False

            try:
                btns = driver.find_elements(*submit_loc)
                submit_visible = any(b.is_displayed() for b in btns)
                submit_disabled = any((b.get_attribute("disabled") is not None) or (b.get_attribute("aria-disabled") == "true") for b in btns)
            except Exception:
                submit_visible = False
                submit_disabled = False

            # Sortie si le bouton n'est plus visible, est désactivé, ou si Review a disparu
            if (not submit_visible) or submit_disabled or (not still_review):
                break

            time.sleep(1.0)

        # Petit délai tampon pour laisser la redirection éventuelle se faire
        time.sleep(1.0)

    except Exception as e:
        print(f"ℹ️  Submit non confirmé : {e} — on poursuit malgré tout.")

    return True

def part3_process_each_job_and_save(driver: webdriver.Chrome, cfg: SeleniumConfig, jobs: List[Dict]) -> None:
    """
    Pour chaque job de la liste :
    - Ouvre le lien dans un nouvel onglet (un seul onglet job à la fois)
    - Extrait les détails, génère un texte EN et sauvegarde
    - Tente la candidature via le flux Apply
    - Ferme l’onglet et revient aux résultats
    """
    profile_corpus = read_profile_corpus(PROFILE_DOCS_DIR)

    base_tab = driver.current_window_handle
    applied_count = 0

    for idx, job in enumerate(jobs, 1):
        url = job.get("url")
        if not url:
            continue

        print(f"\n===== Offre {idx:02d}/20 =====")
        print(f"URL: {url}")

        # Ouvrir dans un nouvel onglet
        driver.switch_to.window(base_tab)
        driver.execute_script("window.open(arguments[0], '_blank');", url)
        WebDriverWait(driver, 10).until(lambda d: len(d.window_handles) >= 2)
        new_tab = [h for h in driver.window_handles if h != base_tab][-1]
        driver.switch_to.window(new_tab)

        # Extraire + sauvegarder la lettre
        try:
            details = extract_job_page_details(driver, cfg)
            for k in ["title", "location"]:
                if not details.get(k):
                    details[k] = job.get(k, "")
            details["url"] = url

            base_name_bits = []
            if details.get("title"):
                base_name_bits.append(clean_slug(details["title"]))
            if details.get("req_id"):
                base_name_bits.append(details["req_id"])
            if not base_name_bits:
                base_name_bits.append(f"job_{idx:02d}")
            base_name = "motivation_Job_" + "_".join(base_name_bits) + ".txt"

        except Exception as e:
            print(f"⚠️ Erreur extraction/sauvegarde sur {url}: {e}")
            debug_pause(cfg, str(e))

        # ======= Flux Apply =======
        try:
            did_apply = apply_flow_for_current_job(driver, cfg)
            if did_apply:
                applied_count += 1
                print(f"✅ Candidature tentée (compteur: {applied_count}/20).")
            else:
                print("⏭️  Offre sautée (pas d’Apply ou déjà 'View Application').")
        except Exception as e:
            print(f"⚠️ Erreur pendant le flux Apply: {e}")
            debug_pause(cfg, str(e))

        # Fermer l’onglet et revenir
        try:
            driver.close()
        except Exception:
            pass
        driver.switch_to.window(base_tab)

        # Arrêt si on a déjà traité 20 offres (sécurité)
        if idx >= 20 or applied_count >= 20:
            print("🛑 Limite atteinte (20 offres).")
            break

# =========================
# NOUVEAU — Pagination
# =========================

def collect_jobs_on_current_page(driver: webdriver.Chrome, cfg: SeleniumConfig) -> List[Dict]:
    """
    Récupère jusqu'à 20 offres visibles sur la page courante (sans toucher aux filtres).
    """
    wait = WebDriverWait(driver, cfg.default_wait_s)
    wait.until(EC.presence_of_all_elements_located((By.CSS_SELECTOR, 'a[data-automation-id="jobTitle"]')))
    time.sleep(1.0)
    links = [a for a in driver.find_elements(By.CSS_SELECTOR, 'a[data-automation-id="jobTitle"]') if a.is_displayed()]
    jobs: List[Dict] = []
    for a in links[:20]:
        title = text_or_empty(a)
        href = a.get_attribute("href") or ""
        if href.startswith("/"):
            href = urljoin(ROOT_URL, href)
        jobs.append({"title": title, "url": href})
    return jobs

def go_to_next_results_page_if_any(driver: webdriver.Chrome, cfg: SeleniumConfig) -> bool:
    """
    Scroll en bas et clique la flèche '>' (chevron droite) si elle existe.
    Retourne True seulement si on a détecté un changement réel de page (numéro actif, texte 1-20 of N, ou 1ère offre).
    """

    wait = WebDriverWait(driver, cfg.default_wait_s)

    def get_pagination_state():
        """Retourne (current_page_label, outof_text, first_job_href)."""
        # page active
        current_page_label = ""
        try:
            navs = driver.find_elements(By.CSS_SELECTOR, "nav[aria-label='pagination']")
            if navs:
                nav = navs[-1]  # pagination bas de page
                active_btns = nav.find_elements(By.CSS_SELECTOR, "button[aria-current='page']")
                if active_btns:
                    current_page_label = active_btns[0].get_attribute("aria-label") or active_btns[0].text.strip()
        except Exception:
            pass

        # compteur "1 - 20 of N jobs"
        outof_text = ""
        try:
            outof_text = driver.find_element(By.CSS_SELECTOR, "[data-automation-id='jobOutOfText']").text.strip()
        except Exception:
            pass

        # premier lien d'offre
        first_job_href = ""
        try:
            links = driver.find_elements(By.CSS_SELECTOR, 'a[data-automation-id="jobTitle"]')
            links = [a for a in links if a.is_displayed()]
            if links:
                first_job_href = links[0].get_attribute("href") or ""
        except Exception:
            pass

        return (current_page_label, outof_text, first_job_href)

    # État avant clic
    before_state = get_pagination_state()

    # Amener la pagination en vue (dernier nav)
    try:
        navs = driver.find_elements(By.CSS_SELECTOR, "nav[aria-label='pagination']")
        if navs:
            driver.execute_script("arguments[0].scrollIntoView({block:'center'});", navs[-1])
            time.sleep(0.4)
        else:
            driver.execute_script("window.scrollTo(0, document.body.scrollHeight);")
            time.sleep(0.6)
    except Exception:
        pass

    # Trouver le bouton "next" du DERNIER nav
    next_btn = None
    try:
        navs = driver.find_elements(By.CSS_SELECTOR, "nav[aria-label='pagination']")
        if navs:
            nav = navs[-1]
            # sélecteurs robustes
            for sel in [
                "button[data-uxi-widget-type='stepToNextButton']",
                "button[data-uxi-element-id='next']",
                "button[aria-label='next']",
            ]:
                cand = nav.find_elements(By.CSS_SELECTOR, sel)
                if cand and cand[0].is_displayed():
                    next_btn = cand[0]
                    break
        # dernier fallback via SVG
        if not next_btn:
            next_btn = driver.find_element(
                By.XPATH,
                "//nav[@aria-label='pagination']//svg[contains(@class,'wd-icon-chevron-right-small')]/ancestor::*[self::button or self::a]"
            )
    except Exception:
        next_btn = None

    if not next_btn:
        print("ℹ️ Aucune page suivante détectée (pas de flèche '>'). Fin de la pagination.")
        return False

    # si désactivé → dernière page
    try:
        if (next_btn.get_attribute("aria-disabled") or "").lower() == "true" or next_btn.get_attribute("disabled") is not None:
            print("ℹ️ Bouton 'next' désactivé — dernière page atteinte. Fin de la pagination.")
            return False
    except Exception:
        pass

    # Clic (robuste)
    try:
        driver.execute_script("arguments[0].scrollIntoView({block:'center'});", next_btn)
        time.sleep(0.1)
    except Exception:
        pass

    clicked = False
    try:
        try:
            next_btn = WebDriverWait(driver, 3).until(EC.element_to_be_clickable((By.XPATH, ".//self::*")))
        except Exception:
            pass
        clicked = robust_click(driver, next_btn)
    except Exception:
        clicked = False

    if not clicked:
        try:
            driver.execute_script("arguments[0].click();", next_btn)
            clicked = True
        except Exception:
            clicked = False

    if not clicked:
        print("ℹ️ Impossible de cliquer sur la flèche '>' — pagination ignorée pour cette page.")
        return False

    print("➡️ Passage à la page suivante…")

    # Attendre un changement RÉEL d'état (jusqu'à 20 s)
    start = time.time()
    while time.time() - start < 20:
        time.sleep(0.6)
        after_state = get_pagination_state()

        # 1) la page active a changé ?
        if before_state[0] and after_state[0] and (after_state[0] != before_state[0]):
            return True

        # 2) le texte "1 - 20 of N jobs" a changé ?
        if before_state[1] and after_state[1] and (after_state[1] != before_state[1]):
            return True

        # 3) le premier job a changé ?
        if before_state[2] and after_state[2] and (after_state[2] != before_state[2]):
            return True

    # Aucun signal de changement → on considère que la page n'a pas bougé
    print("ℹ️ Aucun changement détecté après le clic 'next' — on considère qu'il n'y a plus de page suivante.")
    return False

# =========================
# PARTIE 4 — ORCHESTRATION (ne ferme rien) + Pagination
# =========================

def run_bot() -> int:
    cfg = SeleniumConfig(headless=False, pause_on_error=True)
    creds = Credentials(email="YourEmail@YourEmail.com", password="YourPassword")  # fournis

    driver = build_driver(cfg)
    try:
        # Partie 1 — Login
        part1_login(driver, creds, cfg)
        print("✅ Connexion effectuée. Page actuelle :", driver.current_url)

        # Partie 2 — Filtrer Israel → View Jobs → récupérer 20 premières offres
        jobs = part2_select_israel_and_collect_20(driver, cfg)
        print(f"✅ {len(jobs)} offres récupérées (Israel).")

        # Partie 3 — Traiter la page courante
        part3_process_each_job_and_save(driver, cfg, jobs)

        # NOUVEAU — Boucle pagination : tant qu'il y a une page suivante, on la traite
        page_num = 1
        while go_to_next_results_page_if_any(driver, cfg):
            page_num += 1
            print(f"\n===== Page {page_num} =====")
            jobs_next = collect_jobs_on_current_page(driver, cfg)
            print(f"📄 {len(jobs_next)} offres détectées sur la page {page_num}.")
            part3_process_each_job_and_save(driver, cfg, jobs_next)

        # ⚠️ On NE se déconnecte PAS et on NE ferme PAS le navigateur.
        print("ℹ️ Fin : plus de page suivante. Le navigateur reste ouvert. Ferme la fenêtre manuellement quand tu as fini.")
        return 0

    except Exception as e:
        print("❌ Erreur :", e)
        # Même en cas d’erreur, on ne ferme pas automatiquement le navigateur.
        debug_pause(cfg, f"Exception fatale: {e}")
        return 1

# Pas de finally avec driver.quit() pour garder la fenêtre ouverte.

if __name__ == "__main__":
    sys.exit(run_bot())

