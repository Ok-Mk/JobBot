#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
Bot d'automatisation de candidatures IAI
---------------------------------------

⚠️ Utilisation à vos risques et périls. Respectez les CGU du site IAI et la réglementation locale.
Le script n'essaie PAS de contourner les CAPTCHA. Il se met en pause pour vous laisser les résoudre,
puis reprend lorsque vous appuyez sur Entrée dans la console.

Prérequis:
    pip install selenium webdriver-manager

Exemples d'exécution:
    python bot_candidature_iai.py            # traite TOUTES les pages jusqu'à la fin
    python bot_candidature_iai.py --pages 3  # traite 3 pages à partir de la page 1
    python bot_candidature_iai.py --start 2  # commence à la page 2, continue jusqu'à la fin
    python bot_candidature_iai.py --headless  # lance Chrome en mode headless (peut être moins fiable)

Paramètres de candidature (modifiez au besoin) :
    - Prénom: "Mikhael Joseph Nessim"
    - Nom:    "Ouaknine"
    - ID:     "340907880"
    - Email:  "ouakninemikhael@gmail.com"
    - Tel:    "0537081537"
    - CV:     "IAI_AllMerged.pdf" (dans le dossier courant par défaut)
"""

from __future__ import annotations
import os
import time
import traceback
import argparse
from dataclasses import dataclass
from typing import List, Optional, Set
from urllib.parse import urlparse, parse_qs, urlencode, urlunparse

from selenium import webdriver
from selenium.webdriver.common.by import By
from selenium.webdriver.chrome.options import Options
from selenium.webdriver.chrome.service import Service
from selenium.webdriver.support.ui import WebDriverWait
from selenium.webdriver.support import expected_conditions as EC
from selenium.common.exceptions import (
    NoSuchElementException,
    TimeoutException,
    ElementClickInterceptedException,
    ElementNotInteractableException,
    WebDriverException,
)
from webdriver_manager.chrome import ChromeDriverManager


# ------------------------- Config & Constantes ------------------------- #

BASE_LIST_URL = "https://jobs.iai.co.il/jobs/?pr=41"
BASE_DOMAIN = "https://jobs.iai.co.il"
THANK_YOU_PATH = "/thank-you/"

# Données du candidat
FIRST_NAME = "Mikhael Joseph Nessim"
LAST_NAME = "Ouaknine"
ID_NUMBER = "340907880"
EMAIL = "ouakninemikhael@gmail.com"
PHONE = "0537081537"
CV_FILENAME = "IAI_AllMerged.pdf"  # chemin relatif ou absolu

WAIT_SHORT = 5
WAIT_MED = 12
WAIT_LONG = 20
SCROLL_PAUSE = 0.4


def build_driver(headless: bool = False) -> webdriver.Chrome:
    """Construit un driver Chrome (Selenium 4 + webdriver-manager)."""
    options = Options()
    if headless:
        options.add_argument("--headless=new")
    options.add_argument("--start-maximized")
    options.add_argument("--window-size=1366,900")
    options.add_argument("--disable-gpu")
    options.add_argument("--no-sandbox")
    options.add_argument("--disable-blink-features=AutomationControlled")
    options.add_argument("--lang=fr-FR")
    # Laisser la fenêtre ouverte après la fin du script (pratique pour debug)
    options.add_experimental_option("detach", True)

    service = Service(ChromeDriverManager().install())
    driver = webdriver.Chrome(service=service, options=options)
    driver.set_page_load_timeout(60)
    return driver


# ------------------------- Modèles ------------------------- #

@dataclass
class JobCard:
    title: str
    application_url: Optional[str]
    job_url: Optional[str]
    description: str
    details: List[str]


# ------------------------- Bot ------------------------- #

class IAIApplicationBot:
    def __init__(self, start_page: int = 1, max_pages: int = 0, headless: bool = False):
        self.start_page = max(1, start_page)
        self.max_pages = max(0, max_pages)  # 0 = jusqu'à la dernière page
        self.headless = headless
        self.driver: Optional[webdriver.Chrome] = None
        self.wait: Optional[WebDriverWait] = None
        self.main_handle: Optional[str] = None  # onglet de la liste d'offres
        self.last_list_url: Optional[str] = None
        self.visited_urls: Set[str] = set()

    # ------------------------- Setup & utils ------------------------- #
    def start_browser(self) -> None:
        print("[INFO] Démarrage de Chrome…")
        self.driver = build_driver(self.headless)
        self.wait = WebDriverWait(self.driver, WAIT_LONG)

    def safe_find(self, by: By, value: str, timeout: int = WAIT_MED):
        try:
            return WebDriverWait(self.driver, timeout).until(EC.presence_of_element_located((by, value)))
        except TimeoutException:
            return None

    def safe_find_all(self, by: By, value: str, timeout: int = WAIT_MED):
        try:
            WebDriverWait(self.driver, timeout).until(EC.presence_of_element_located((by, value)))
            return self.driver.find_elements(by, value)
        except TimeoutException:
            return []

    def scroll_into_view(self, element) -> None:
        try:
            self.driver.execute_script("arguments[0].scrollIntoView({behavior: 'smooth', block: 'center'});", element)
            time.sleep(SCROLL_PAUSE)
        except WebDriverException:
            pass

    def gentle_scroll_page(self, steps: int = 6) -> None:
        """Fait défiler la page vers le bas par petits incréments (pour forcer le chargement paresseux)."""
        try:
            for _ in range(steps):
                self.driver.execute_script("window.scrollBy(0, Math.floor(window.innerHeight*0.8));")
                time.sleep(SCROLL_PAUSE)
        except WebDriverException:
            pass

    def absolute_url(self, href: Optional[str]) -> Optional[str]:
        if not href:
            return None
        if href.startswith("http"):
            return href
        if href.startswith("/"):
            return BASE_DOMAIN + href
        return BASE_DOMAIN + "/" + href

    def _element_is_displayed(self, el) -> bool:
        try:
            return el.is_displayed()
        except Exception:
            return False

    # ------------------------- Navigation: par URL (ÉTAPE 7) ------------------------- #
    def goto_list_url(self, url: str) -> None:
        print(f"[PAGE] Ouverture de la liste: {url}")
        self.driver.get(url)
        self.main_handle = self.driver.current_window_handle
        self.safe_find(By.CSS_SELECTOR, ".vue-jobs .jobs-wrap", timeout=WAIT_LONG)
        self.gentle_scroll_page(steps=8)
        self.last_list_url = self.driver.current_url
        self.visited_urls.add(self.last_list_url)

    def compute_next_list_url(self, current_url: str) -> Optional[str]:
        """Implémente 7.1–7.2–7.3 :
        - Si l'URL est exactement BASE_LIST_URL → pg=2
        - Si l'URL est de la forme BASE_LIST_URL&pg=X (1≤X≤1000) → pg=X+1
        Retourne l'URL suivante, sinon None.
        """
        try:
            parsed = urlparse(current_url)
            if parsed.netloc != urlparse(BASE_LIST_URL).netloc:
                return None
            if parsed.path != "/jobs/":
                return None
            qs = parse_qs(parsed.query)
            if qs.get("pr", [None])[0] != "41":
                return None

            if "pg" not in qs:
                next_pg = 2
            else:
                try:
                    x = int(qs["pg"][0])
                except Exception:
                    return None
                if x < 1 or x > 1000:
                    return None
                next_pg = x + 1

            qs["pg"] = [str(next_pg)]
            new_query = urlencode({k: v[0] for k, v in qs.items()})
            next_url = urlunparse((parsed.scheme, parsed.netloc, parsed.path, "", new_query, ""))
            return next_url
        except Exception:
            return None

    # ------------------------- Scraping ------------------------- #
    def scrape_jobs_on_page(self) -> List[JobCard]:
        print("[INFO] Récupération des descriptifs d'offres sur la page…")
        cards = self.safe_find_all(By.CSS_SELECTOR, ".vue-jobs .jobs-wrap .vue-job")
        jobs: List[JobCard] = []
        for idx, card in enumerate(cards, start=1):
            # Titre
            try:
                title_el = card.find_element(By.CSS_SELECTOR, "h3 a")
                title = title_el.text.strip()
            except NoSuchElementException:
                title = "(Titre introuvable)"

            # Liens
            app_link_el = None
            job_link_el = None
            try:
                app_link_el = card.find_element(By.CSS_SELECTOR, ".btns-wrap a[href^='/application/']")
            except NoSuchElementException:
                pass
            try:
                job_link_el = card.find_element(By.CSS_SELECTOR, "h3 a[href^='/job/']")
            except NoSuchElementException:
                pass

            # Description
            try:
                desc = card.find_element(By.CSS_SELECTOR, ".card-body p").text.strip()
            except NoSuchElementException:
                desc = "(Description introuvable sur la carte)"

            # Détails (lieu, type, catégorie…)
            details_texts = []
            for li in card.find_elements(By.CSS_SELECTOR, ".job-details li span"):
                txt = li.text.strip()
                if txt:
                    details_texts.append(txt)

            job = JobCard(
                title=title,
                application_url=self.absolute_url(app_link_el.get_attribute("href")) if app_link_el else None,
                job_url=self.absolute_url(job_link_el.get_attribute("href")) if job_link_el else None,
                description=desc,
                details=details_texts,
            )
            jobs.append(job)

            # Affichage dans la console
            print(f"Offre {idx} ——")
            print(f"Titre : {job.title}")
            if job.details:
                print("Détails : " + " | ".join(job.details))
            print(f"Lien fiche : {job.job_url or '(absent)'}")
            print(f"Lien candidature : {job.application_url or '(absent)'}")
            print("Descriptif :" + (job.description[:1000] + ("…" if len(job.description) > 1000 else "")))

        if not jobs:
            print("[WARN] Aucune offre détectée sur cette page.")
        return jobs

    # ------------------------- Candidature ------------------------- #
    def apply_to_jobs_on_page(self, jobs: List[JobCard]) -> None:
        for idx, job in enumerate(jobs, start=1):
            print(f"[APPLY] Traitement de l'offre {idx}: {job.title}")
            if not job.application_url:
                print("[SKIP] Lien de candidature introuvable pour cette offre. Passage à la suivante.")
                continue
            try:
                self.open_in_new_tab(job.application_url)
                success = self.fill_and_submit_application()
            except Exception:
                print("[ERREUR] Une erreur imprévue est survenue pendant la candidature :")
                traceback.print_exc()
                print("[PAUSE] Le programme est en pause. Ne fermez pas la page. Appuyez sur Entrée pour continuer…")
                try:
                    input()
                except Exception:
                    pass
                success = False
            finally:
                # Étape 6 — Ne fermer l'onglet de candidature QUE si l'on est bien redirigé vers /thank-you/
                self.finish_tab(success)

    def open_in_new_tab(self, url: str) -> None:
        print(f"[NAV] Ouverture de l'onglet candidature: {url}")
        self.driver.execute_script("window.open(arguments[0], '_blank');", url)
        self.driver.switch_to.window(self.driver.window_handles[-1])
        # Attendre que la page soit chargée (form présent si possible)
        if not self.safe_find(By.CSS_SELECTOR, "form", timeout=WAIT_LONG):
            self.safe_find(By.TAG_NAME, "body", timeout=WAIT_LONG)
        self.gentle_scroll_page(steps=4)

    def fill_and_submit_application(self) -> bool:
        # 4.1 firstName
        self._fill_text_field_by_id("firstName", FIRST_NAME, label="prénom")
        # 4.2 lastName
        self._fill_text_field_by_id("lastName", LAST_NAME, label="nom")
        # 4.3 idNumber
        self._fill_text_field_by_id("idNumber", ID_NUMBER, label="numéro d'identité")
        # 4.4 email
        self._fill_text_field_by_id("email", EMAIL, label="email")
        # 4.5 phone
        self._fill_text_field_by_id("phone", PHONE, label="téléphone")

        # 4.6 Radio hasRelative = no
        try:
            radio_no = self.safe_find(By.CSS_SELECTOR, "input[type='radio'][name='hasRelative'][value='no']", timeout=WAIT_MED)
            if radio_no:
                self.scroll_into_view(radio_no)
                try:
                    radio_no.click()
                    print("[OK] Case 'Je n'ai pas de proches dans l'entreprise' cochée.")
                except (ElementClickInterceptedException, ElementNotInteractableException):
                    self.driver.execute_script("arguments[0].click();", radio_no)
                    print("[OK] Case 'Je n'ai pas de proches dans l'entreprise' cochée (via JS).")
            else:
                print("[INFO] La question 'קרובי משפחה' n'apparaît pas. Passage à l'étape suivante.")
        except Exception:
            print("[WARN] Impossible de traiter la question 'קרובי משפחה'. Passage à l'étape suivante.")

        # 4.7 Upload du CV
        self._upload_cv()

        # Déclenche validations front + consentements potentiels
        self._fix_common_validation_states()
        self._check_possible_consent_checkboxes()
        self._touch_form_validation()

        # 5. Soumission + gestion CAPTCHA + attente de /thank-you/
        success = self._submit_with_captcha_and_wait_thank_you()
        return success

    def _fill_text_field_by_id(self, field_id: str, value: str, label: str) -> None:
        el = self.safe_find(By.ID, field_id, timeout=WAIT_MED)
        if not el:
            print(f"[INFO] La case '{label}' (id={field_id}) n'apparaît pas. Passage à la suivante.")
            return
        try:
            self.scroll_into_view(el)
            el.clear()
            el.send_keys(value)
            # Déclenche les événements front habituels + nettoie l'état invalide si présent
            self.driver.execute_script(
                "arguments[0].dispatchEvent(new Event('input', {bubbles:true}));"
                "arguments[0].dispatchEvent(new Event('change', {bubbles:true}));"
                "arguments[0].dispatchEvent(new Event('blur', {bubbles:true}));"
                "arguments[0].classList.remove('is-invalid');"
                "arguments[0].setAttribute('aria-invalid','false');",
                el,
            )
            print(f"[OK] Champ '{label}' rempli.")
        except Exception:
            print(f"[WARN] Impossible de remplir le champ '{label}'.")

    def _upload_cv(self) -> None:
        file_input = self.safe_find(By.CSS_SELECTOR, "input#upload_cv[type='file']", timeout=WAIT_MED)
        if not file_input:
            print("[INFO] La zone d'upload du CV (id=upload_cv) n'apparaît pas. Passage à la suite.")
            return

        # Résolution du chemin du CV
        cv_path = CV_FILENAME
        if not os.path.isabs(cv_path):
            cv_path = os.path.abspath(os.path.join(os.getcwd(), cv_path))

        if not os.path.exists(cv_path):
            print(f"[ALERTE] Le fichier CV '{CV_FILENAME}' est introuvable à l'emplacement: {cv_path}")
            print("         Merci de placer ce fichier au bon endroit, puis appuyez sur Entrée pour réessayer cet upload…")
            try:
                input()
            except Exception:
                pass
            if not os.path.exists(cv_path):
                print("[SKIP] Fichier toujours introuvable. Étape d'upload ignorée.")
                return

        try:
            self.scroll_into_view(file_input)
            try:
                file_input.click()
            except Exception:
                pass
            file_input.send_keys(cv_path)
            # Déclenche les événements front
            self.driver.execute_script(
                "arguments[0].dispatchEvent(new Event('change', {bubbles:true}));"
                "arguments[0].dispatchEvent(new Event('input', {bubbles:true}));"
                "arguments[0].dispatchEvent(new Event('blur', {bubbles:true}));"
                "arguments[0].classList.remove('is-invalid');"
                "arguments[0].setAttribute('aria-invalid','false');",
                file_input,
            )
            print(f"[OK] CV uploadé via le champ fichier: {cv_path}")
        except Exception:
            print("[WARN] Échec lors de l'upload du CV. Vous pouvez téléverser manuellement puis poursuivre.")
            print("[PAUSE] Le programme est en pause. Appuyez sur Entrée pour continuer…")
            try:
                input()
            except Exception:
                pass

    def _fix_common_validation_states(self) -> None:
        """Déclenche les validations front et coche les cases requises si présentes.
        Corrige les cas où le bouton reste en 'aria-disabled=true' alors qu'aucun CAPTCHA n'est visible.
        """
        # 1) Déclencher blur/change sur tous les inputs requis visibles
        try:
            inputs = self.driver.find_elements(By.CSS_SELECTOR, "input[required], select[required], textarea[required]")
            for el in inputs:
                if not self._element_is_displayed(el):
                    continue
                try:
                    self.scroll_into_view(el)
                    self.driver.execute_script(
                        "arguments[0].dispatchEvent(new Event('input', {bubbles:true}));"
                        "arguments[0].dispatchEvent(new Event('change', {bubbles:true}));"
                        "arguments[0].dispatchEvent(new Event('blur', {bubbles:true}));"
                        "arguments[0].classList.remove('is-invalid');"
                        "arguments[0].setAttribute('aria-invalid','false');",
                        el,
                    )
                except Exception:
                    pass
        except Exception:
            pass

        # 2) Cocher d'éventuelles cases à cocher requises (ex: consentement)
        try:
            checkboxes = self.driver.find_elements(By.CSS_SELECTOR, "input[type='checkbox'][required]")
            for cb in checkboxes:
                try:
                    if not cb.is_selected() and self._element_is_displayed(cb):
                        self.scroll_into_view(cb)
                        try:
                            cb.click()
                        except Exception:
                            self.driver.execute_script("arguments[0].click();", cb)
                        print("[OK] Case à cocher obligatoire cochée.")
                except Exception:
                    pass
        except Exception:
            pass

        # 3) Attendre un court instant que les validations s'appliquent
        time.sleep(1.2)

    def _check_possible_consent_checkboxes(self) -> None:
        """Sur certains formulaires, des consentements ne sont pas marqués 'required'. On coche prudemment
        les cases dont le label/la name évoquent un consentement.
        """
        try:
            form = self._get_form()
            if not form:
                return
            # Chercher des cases à cocher non cochées dans le formulaire
            cbs = form.find_elements(By.CSS_SELECTOR, "input[type='checkbox']")
            for cb in cbs:
                try:
                    if cb.is_selected() or not self._element_is_displayed(cb):
                        continue
                    # Heuristiques sur id/name/label
                    ident = (cb.get_attribute('id') or '') + ' ' + (cb.get_attribute('name') or '')
                    label_text = ''
                    if cb.get_attribute('id'):
                        try:
                            lab = form.find_element(By.CSS_SELECTOR, f"label[for='{cb.get_attribute('id')}']")
                            label_text = (lab.text or '').strip()
                        except Exception:
                            pass
                    tokens = (ident + ' ' + label_text).lower()
                    # mots-clés FR/EN/HE: agree/consent/privacy/terms/מאשר/מאשרת/תנאים/פרטיות
                    if any(k in tokens for k in [
                        'agree','consent','privacy','terms','rgpd','newsletter',
                        'מאשר','מאשרת','תקנון','תנאים','פרטיות','הסכמה']):
                        # On coche uniquement s'il semble raisonnable
                        self.scroll_into_view(cb)
                        try:
                            cb.click()
                        except Exception:
                            self.driver.execute_script("arguments[0].click();", cb)
                        print("[OK] Case de consentement cochée.")
                except Exception:
                    pass
        except Exception:
            pass

    def _touch_form_validation(self) -> None:
        """Force l'évaluation HTML5 de la validité du formulaire pour mettre à jour les états."""
        try:
            form = self._get_form()
            if not form:
                return
            # Déclencher reportValidity pour afficher/mettre à jour les erreurs natives
            self.driver.execute_script("arguments[0].reportValidity && arguments[0].reportValidity();", form)
        except Exception:
            pass

    def _get_form(self):
        try:
            forms = self.driver.find_elements(By.CSS_SELECTOR, "form")
            return forms[0] if forms else None
        except Exception:
            return None

    def _is_button_disabled(self, btn) -> bool:
        try:
            aria = (btn.get_attribute("aria-disabled") or "").lower()
            disabled_prop = self.driver.execute_script("return arguments[0].disabled === true;", btn)
        except Exception:
            disabled_prop = False
        cls = (btn.get_attribute("class") or "")
        has_disabled_class = "disabled" in cls.split()
        return aria == "true" or disabled_prop or has_disabled_class

    # ------------------------- Soumission + attente /thank-you/ ------------------------- #
    def _submit_with_captcha_and_wait_thank_you(self, wait_total: int = 45) -> bool:
        """Clique le bouton de soumission, gère la pause CAPTCHA et attend la redirection /thank-you/.
        Retourne True si la redirection /thank-you/ est atteinte (dans l'onglet courant ou un autre), sinon False.
        IMPORTANT: Ne JAMAIS mettre en pause pour CAPTCHA si on est déjà sur /thank-you/.
        """
        submit_btn = self.safe_find(By.CSS_SELECTOR, "button._g-recaptcha[type='submit']", timeout=WAIT_MED)
        if not submit_btn:
            print("[INFO] Bouton de soumission introuvable. Impossible de valider automatiquement.")
            print("[PAUSE] Vous pouvez valider manuellement, puis appuyer sur Entrée pour poursuivre…")
            try:
                input()
            except Exception:
                pass
            # Après action manuelle, essayer quand même d'attendre /thank-you/
            return self._wait_for_thank_you_redirect(wait_total)

        self.scroll_into_view(submit_btn)

        # Attendre qu'il devienne cliquable naturellement
        try:
            WebDriverWait(self.driver, WAIT_LONG).until(EC.element_to_be_clickable((By.CSS_SELECTOR, "button._g-recaptcha[type='submit']")))
        except TimeoutException:
            pass

        # Si désactivé et aucun CAPTCHA visible, tenter de lever l'état 'disabled'
        if self._is_button_disabled(submit_btn) and not self._recaptcha_visible():
            print("[INFO] Bouton signalé désactivé sans CAPTCHA visible → tentative d'activation contrôlée…")
            try:
                self.driver.execute_script(
                    "arguments[0].removeAttribute('aria-disabled');"
                    "arguments[0].disabled = false;"
                    "arguments[0].classList.remove('disabled');",
                    submit_btn,
                )
                time.sleep(0.5)
            except Exception:
                pass

        # Clic
        try:
            try:
                submit_btn.click()
            except (ElementClickInterceptedException, ElementNotInteractableException):
                self.driver.execute_script("arguments[0].click();", submit_btn)
            print("[ACTION] Clic sur 'הגשת מועמדות' envoyé.")
        except Exception:
            print("[WARN] Impossible de cliquer sur le bouton de soumission. Essayez manuellement, puis appuyez sur Entrée.")
            try:
                input()
            except Exception:
                pass

        # ⚠️ Vérifier d'abord si la redirection /thank-you/ arrive vite
        initial_wait = min(12, wait_total)
        success = self._wait_for_thank_you_redirect(initial_wait)
        if success:
            return True

        # Ensuite seulement: pause si un reCAPTCHA est visible ET qu'on n'est pas sur /thank-you/
        if self._recaptcha_visible():
            print("[INFO] CAPTCHA détecté.")
            print("[PAUSE] Complétez le CAPTCHA dans le navigateur, puis appuyez sur Entrée pour reprendre…")
            try:
                input()
            except Exception:
                pass

        # Ré-attente du reste du temps pour /thank-you/
        remaining = max(0, wait_total - initial_wait)
        success = self._wait_for_thank_you_redirect(remaining)
        if success:
            print("[OK] Redirection /thank-you/ détectée : candidature soumise.")
        else:
            print("[INFO] Pas de redirection /thank-you/ détectée dans le délai imparti.")
        return success

    def _wait_for_thank_you_redirect(self, max_wait: int = 45) -> bool:
        """Attend que l'URL de l'onglet courant ou d'un autre onglet contienne /thank-you/."""
        start = time.time()
        while time.time() - start < max_wait:
            try:
                # 1) Vérifier l'onglet courant
                cur_url = (self.driver.current_url or "")
                if THANK_YOU_PATH in cur_url:
                    return True
                # 2) Vérifier les autres onglets
                handles = self.driver.window_handles
                for h in handles:
                    try:
                        self.driver.switch_to.window(h)
                        u = (self.driver.current_url or "")
                        if THANK_YOU_PATH in u:
                            return True
                    except Exception:
                        pass
                # 3) Attendre et recommencer
                time.sleep(1)
            except Exception:
                time.sleep(1)
        # Revenir sur l'onglet principal si connu
        if self.main_handle and self.main_handle in self.driver.window_handles:
            try:
                self.driver.switch_to.window(self.main_handle)
            except Exception:
                pass
        return False

    def _recaptcha_visible(self) -> bool:
        """Détection heuristique du reCAPTCHA. Ne considère jamais qu'il y a un captcha sur /thank-you/."""
        try:
            # Si on est déjà sur la page de remerciement, ignorer tout signalement de captcha
            cur_url = (self.driver.current_url or "")
            if THANK_YOU_PATH in cur_url:
                return False
            # iframes reCAPTCHA classiques
            iframes = self.driver.find_elements(By.CSS_SELECTOR, "iframe[src*='recaptcha']")
            if iframes:
                return True
            # Widgets visibles éventuels
            boxes = self.driver.find_elements(By.CSS_SELECTOR, ".g-recaptcha, div[role='presentation'][aria-label*='recaptcha']")
            return len(boxes) > 0
        except Exception:
            return False

    # ------------------------- Étape 6: gestion des onglets ------------------------- #
    def finish_tab(self, success: bool) -> None:
        """Ferme l'onglet candidature UNIQUEMENT si succès (/thank-you/). Sinon, le laisse ouvert
        et revient à l'onglet de la liste pour continuer.
        """
        try:
            current = self.driver.current_window_handle
        except Exception:
            current = None

        if success:
            # Si on est sur /thank-you/ dans un onglet, fermer cet onglet
            try:
                if len(self.driver.window_handles) > 1 and current:
                    self.driver.close()
            except Exception:
                pass
        else:
            print("[NOTE] Onglet conservé pour diagnostic (pas de /thank-you/).")

        # Revenir à l'onglet principal (liste)
        target = None
        if self.main_handle and self.main_handle in self.driver.window_handles:
            target = self.main_handle
        elif self.driver.window_handles:
            target = self.driver.window_handles[0]
        if target:
            try:
                self.driver.switch_to.window(target)
            except Exception:
                pass
        time.sleep(0.4)

    # ------------------------- Orchestration (Étape 7 revisitée) ------------------------- #
    def run(self) -> None:
        self.start_browser()

        # 1) Déterminer l'URL de départ en fonction de --start
        if self.start_page <= 1:
            initial_url = BASE_LIST_URL
        else:
            initial_url = f"{BASE_LIST_URL}&pg={self.start_page}"
        self.goto_list_url(initial_url)

        try:
            while True:
                # Étapes 1→6 sur la page courante
                jobs = self.scrape_jobs_on_page()
                self.apply_to_jobs_on_page(jobs)
                print("[PAGE ✓] Toutes les annonces de la page ont été traitées.")  # 7.4

                # Lire l'URL courante (7.1)
                cur_url = self.driver.current_url
                print(f"[PAGE] URL courante (après traitement): {cur_url}")

                # Calculer l'URL suivante (7.2)
                next_url = self.compute_next_list_url(cur_url)
                if not next_url:
                    print("[INFO] Aucune URL suivante valide calculée (fin des pages ou format inattendu). Arrêt.")
                    break

                # Respecter --pages si > 0 (sinon continuer jusqu'à la fin)
                if self.max_pages > 0:
                    # Compter combien de pages déjà visitées depuis le début
                    pages_done = len([u for u in self.visited_urls if "/jobs/" in u and "pr=41" in u])
                    if pages_done >= self.max_pages + (self.start_page - 1):
                        print(f"[INFO] Limite --pages atteinte ({self.max_pages}). Arrêt.")
                        break

                # Éviter les boucles
                if next_url in self.visited_urls:
                    print(f"[INFO] L'URL suivante a déjà été visitée ({next_url}). Arrêt pour éviter une boucle.")
                    break

                # 7.3 — mémoriser et recharger la nouvelle page dans le MÊME onglet
                self.last_list_url = next_url
                print(f"[PAGE ➜] Passage à la page suivante via URL directe: {next_url}")
                self.goto_list_url(next_url)
        finally:
            print("[FIN] Traitement terminé. Le navigateur reste ouvert (detach=True). Fermez la fenêtre manuellement si besoin.")


# ------------------------- Entrée CLI ------------------------- #

def parse_args() -> argparse.Namespace:
    p = argparse.ArgumentParser(description="Bot d'automatisation de candidatures IAI")
    p.add_argument("--start", type=int, default=1, help="Index de la page de départ (défaut: 1)")
    p.add_argument("--pages", type=int, default=0, help="Nombre de pages à traiter (0 = jusqu'à la dernière page)")
    p.add_argument("--headless", action="store_true", help="Lancer Chrome en mode headless")
    return p.parse_args()


def main():
    args = parse_args()
    bot = IAIApplicationBot(start_page=args.start, max_pages=args.pages, headless=args.headless)
    bot.run()


if __name__ == "__main__":
    main()

