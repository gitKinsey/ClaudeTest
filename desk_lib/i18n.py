"""Interface language. English is the source text; `tr(text)` returns the translation when a language is active and the text is known,
otherwise the English text unchanged. Covers the navigation, common buttons and main headings - not every sentence. Takes effect after a restart."""

LANGS = {"en": "English", "de": "Deutsch", "es": "Español", "fr": "Français"}
_lang = "en"

T = {
    "de": {
        "Home": "Start", "Device": "Gerät", "Virtual Pad": "Virtuelles Pad", "Dashboard": "Übersicht", "Macro Creator": "Makro-Editor",
        "GIF Upload": "GIF-Upload", "Profiles": "Profile", "Info Screen": "Info-Bildschirm", "Scripts": "Skripte", "Automation": "Automatisierung",
        "Save": "Speichern", "Delete": "Löschen", "New": "Neu", "Cancel": "Abbrechen", "Close": "Schließen", "Check": "Prüfen", "Run": "Ausführen",
        "Stop": "Stopp", "Restore": "Wiederherstellen", "Search": "Suchen", "Connect": "Verbinden", "Disconnect": "Trennen",
        "Display and behaviour": "Anzeige und Verhalten", "Brightness": "Helligkeit", "Screen mode": "Bildschirmmodus", "Host OS": "Betriebssystem",
        "Keyboard layout": "Tastaturlayout", "Sync time now": "Uhrzeit jetzt abgleichen", "Pad behaviour": "Pad-Verhalten",
        "Computer actions": "Computer-Aktionen", "Firmware": "Firmware", "Backup and restore": "Sicherung und Wiederherstellung",
        "Recovery and safety": "Wiederherstellung und Sicherheit", "Light theme": "Helles Design", "Searching...": "Suche...",
        "Macro scripts": "Makro-Skripte", "Appearance": "Darstellung", "Language": "Sprache", "Check for updates": "Nach Updates suchen",
        "Layer": "Ebene", "Upload": "Hochladen", "Apply": "Übernehmen", "Test": "Testen", "Dry run": "Probelauf", "Settings": "Einstellungen",
    },
    "es": {
        "Home": "Inicio", "Device": "Dispositivo", "Virtual Pad": "Pad virtual", "Dashboard": "Panel", "Macro Creator": "Editor de macros",
        "GIF Upload": "Subir GIF", "Profiles": "Perfiles", "Info Screen": "Pantalla de info", "Scripts": "Scripts", "Automation": "Automatización",
        "Save": "Guardar", "Delete": "Borrar", "New": "Nuevo", "Cancel": "Cancelar", "Close": "Cerrar", "Check": "Comprobar", "Run": "Ejecutar",
        "Stop": "Parar", "Restore": "Restaurar", "Search": "Buscar", "Connect": "Conectar", "Disconnect": "Desconectar",
        "Display and behaviour": "Pantalla y comportamiento", "Brightness": "Brillo", "Screen mode": "Modo de pantalla", "Host OS": "Sistema operativo",
        "Keyboard layout": "Distribución del teclado", "Sync time now": "Sincronizar la hora", "Pad behaviour": "Comportamiento del pad",
        "Computer actions": "Acciones del ordenador", "Firmware": "Firmware", "Backup and restore": "Copia de seguridad y restauración",
        "Recovery and safety": "Recuperación y seguridad", "Light theme": "Tema claro", "Searching...": "Buscando...",
        "Macro scripts": "Scripts de macros", "Appearance": "Apariencia", "Language": "Idioma", "Check for updates": "Buscar actualizaciones",
        "Layer": "Capa", "Upload": "Subir", "Apply": "Aplicar", "Test": "Probar", "Dry run": "Simulación", "Settings": "Ajustes",
    },
    "fr": {
        "Home": "Accueil", "Device": "Appareil", "Virtual Pad": "Pad virtuel", "Dashboard": "Tableau de bord", "Macro Creator": "Éditeur de macros",
        "GIF Upload": "Envoi de GIF", "Profiles": "Profils", "Info Screen": "Écran d'infos", "Scripts": "Scripts", "Automation": "Automatisation",
        "Save": "Enregistrer", "Delete": "Supprimer", "New": "Nouveau", "Cancel": "Annuler", "Close": "Fermer", "Check": "Vérifier", "Run": "Exécuter",
        "Stop": "Arrêter", "Restore": "Restaurer", "Search": "Rechercher", "Connect": "Connecter", "Disconnect": "Déconnecter",
        "Display and behaviour": "Affichage et comportement", "Brightness": "Luminosité", "Screen mode": "Mode d'écran", "Host OS": "Système d'exploitation",
        "Keyboard layout": "Disposition du clavier", "Sync time now": "Synchroniser l'heure", "Pad behaviour": "Comportement du pad",
        "Computer actions": "Actions de l'ordinateur", "Firmware": "Firmware", "Backup and restore": "Sauvegarde et restauration",
        "Recovery and safety": "Récupération et sécurité", "Light theme": "Thème clair", "Searching...": "Recherche...",
        "Macro scripts": "Scripts de macros", "Appearance": "Apparence", "Language": "Langue", "Check for updates": "Rechercher des mises à jour",
        "Layer": "Couche", "Upload": "Envoyer", "Apply": "Appliquer", "Test": "Tester", "Dry run": "Simulation", "Settings": "Réglages",
    },
}


def _merge_pages():
    from desk_lib.i18n_pages import merged
    for lang, table in merged().items():
        for en, tr_ in table.items():
            T[lang].setdefault(en, tr_)


_merge_pages()


def set_language(code):
    global _lang
    _lang = code if code in LANGS else "en"
    return _lang


def language():
    return _lang


def tr(text):
    if _lang == "en" or not isinstance(text, str) or not text:
        return text
    return T.get(_lang, {}).get(text, text)
