"""Ordonnance Mobile PHARMACIE - application de bureau, 100 % locale (sans Internet).

  python main.py          -> version PC : comptes et donnees stockes sur cet ordinateur
  python main.py --demo   -> mode demo : donnees fictives, effacees a chaque lancement
  python main.py --serveur-seul  -> sans fenetre (ouvre le navigateur par defaut)
(si la fenetre integree ne peut pas s'ouvrir, le navigateur est utilise automatiquement)

Donnees : dossier .ordonnance_mobile/donnees de votre profil utilisateur.
Ce dossier est PARTAGE avec l'application Hopital : les deux logiciels utilisent la meme base.
"""
import os
import sys
import threading
import webbrowser

import backend

STOCKAGE, STOCKAGE_DEMO = "webview-pharmacie", "webview-pharmacie-demo"
TITRE = "Ordonnance Mobile Pharmacie"


def dossier_app():
    base = getattr(sys, "_MEIPASS", os.path.dirname(os.path.abspath(__file__)))
    return os.path.join(base, "app")


def main():
    demo = "--demo" in sys.argv
    home = os.path.join(os.path.expanduser("~"), ".ordonnance_mobile")
    data = os.path.join(home, "demo" if demo else "donnees")
    srv, port = backend.start(dossier_app(), data, demo=demo, port=8768 if demo else 8767, kind="pharmacie")
    url = "http://127.0.0.1:%d/index.html" % port
    print("%s%s : %s\nDonnees : %s" % (TITRE, " [DEMO]" if demo else "", url, data))

    if "--serveur-seul" not in sys.argv:
        try:
            if sys.platform == "win32":
                # Windows : utiliser .NET Framework (present sur tous les Windows 10/11) plutot que .NET moderne,
                # sinon l'erreur "Could not load System.Windows.Forms" peut apparaitre.
                try:
                    import pythonnet
                    pythonnet.load("netfx")
                except Exception:
                    pass
            import webview
            webview.create_window(TITRE + (" - DEMO" if demo else ""), url, width=1200, height=800, min_size=(420, 600))
            webview.start(private_mode=False, storage_path=os.path.join(home, STOCKAGE_DEMO if demo else STOCKAGE))
            srv.shutdown()
            return
        except Exception as e:  # fenetre impossible (ex. System.Windows.Forms introuvable) : on passe par le navigateur
            print("Fenetre integree indisponible (%s) : ouverture dans le navigateur." % str(e).splitlines()[0][:150])
    ouvrir_navigateur(url)


def ouvrir_navigateur(url):
    webbrowser.open(url)
    print("Ouvert dans le navigateur. Fermez la petite fenetre (ou Ctrl+C) pour quitter.")
    try:
        import tkinter as tk
        r = tk.Tk()
        r.title(TITRE)
        r.geometry("440x170")
        tk.Label(r, text=TITRE + "\n\nL'application est ouverte dans votre navigateur.\nFermez cette fenetre pour quitter le logiciel.", justify="center", pady=14).pack()
        tk.Button(r, text="Ouvrir a nouveau", command=lambda: webbrowser.open(url)).pack(side="left", padx=40, pady=10)
        tk.Button(r, text="Quitter", command=r.destroy).pack(side="right", padx=40, pady=10)
        r.mainloop()
    except Exception:
        try:
            threading.Event().wait()
        except KeyboardInterrupt:
            pass


if __name__ == "__main__":
    main()
