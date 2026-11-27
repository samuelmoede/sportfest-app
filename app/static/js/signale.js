// Signale beim Spiel: gemeinsamer Teil fuer die Feldansicht
// (/ergebnisse/feld) und die Seite "Mein Konto" (/konto).
//
// Die Schalter (Ton und Vibration, Countdown, Benachrichtigung) gelten
// grundsaetzlich, nicht je Spiel. Mit Konto liegen sie beim Server
// (window.SIGNAL_EINSTELLUNGEN, gesetzt von der Vorlage); ohne Anmeldung
// merkt sie sich der Browser. Die Erlaubnis fuer Benachrichtigungen gibt der
// Browser dagegen immer nur auf dem Geraet selbst.
(function () {
    'use strict';

    const LOKAL = {
        ton: 'feldansicht_signal',
        countdown: 'feldansicht_countdown',
        benachrichtigung: 'feldansicht_benachrichtigung',
    };
    const SW_PFAD = '/static/js/benachrichtigung-sw.js';

    const mitKonto = () => !!window.SIGNAL_EINSTELLUNGEN;

    function lokal(name) {
        try {
            const wert = localStorage.getItem(LOKAL[name]);
            // Benachrichtigung muss man ohne Konto ausdruecklich einschalten.
            return name === 'benachrichtigung' ? wert === 'an' : wert !== 'aus';
        } catch (e) {
            return name !== 'benachrichtigung';
        }
    }
    function einstellungen() {
        if (mitKonto()) return Object.assign({}, window.SIGNAL_EINSTELLUNGEN);
        return { ton: lokal('ton'), countdown: lokal('countdown'), benachrichtigung: lokal('benachrichtigung') };
    }
    async function speichere(name, wert) {
        if (!mitKonto()) {
            try { localStorage.setItem(LOKAL[name], wert ? 'an' : 'aus'); } catch (e) { /* egal */ }
            return true;
        }
        const antwort = await fetch('/konto/signale', {
            method: 'POST',
            credentials: 'same-origin',
            headers: { 'Content-Type': 'application/json' },
            body: JSON.stringify({ [name]: !!wert }),
        });
        if (!antwort.ok) return false;
        const daten = await antwort.json();
        window.SIGNAL_EINSTELLUNGEN = daten.einstellungen;
        return true;
    }

    // ------------------------------------------------------------ Ton
    const SIGNALE = {
        knapp: { toene: [[880, 0.15, 0.1], [880, 0.15, 0]], vibration: [200, 100, 200] },
        ende: { toene: [[1000, 0.4, 0.15], [1000, 0.4, 0.15], [1000, 1.0, 0]], vibration: [600, 200, 600, 200, 1000] },
    };
    const COUNTDOWN_SEKUNDEN = 5;
    const COUNTDOWN_VIBRATION = 120;
    let audio = null;
    // Browser spielen Ton erst nach einem Antippen der Seite ab: deshalb
    // wird der Ton beim ersten Tippen vorbereitet.
    function audioBereit() {
        const Klasse = window.AudioContext || window.webkitAudioContext;
        if (!Klasse) return null;
        if (!audio) {
            try {
                // iPhone (Safari 17+): Ton auch bei eingeschaltetem Stummschalter.
                if (navigator.audioSession) navigator.audioSession.type = 'playback';
                audio = new Klasse();
            } catch (e) {
                return null;
            }
        }
        if (audio.state === 'suspended' && audio.resume) audio.resume().catch(() => {});
        return audio;
    }
    function piepe(toene) {
        const ctx = audioBereit();
        if (!ctx) return;
        let zeit = ctx.currentTime + 0.02;
        toene.forEach(([frequenz, dauer, pause]) => {
            const ton = ctx.createOscillator();
            const lautstaerke = ctx.createGain();
            ton.type = 'square';
            ton.frequency.value = frequenz;
            lautstaerke.gain.setValueAtTime(0.0001, zeit);
            lautstaerke.gain.exponentialRampToValueAtTime(0.4, zeit + 0.01);
            lautstaerke.gain.setValueAtTime(0.4, zeit + dauer - 0.02);
            lautstaerke.gain.exponentialRampToValueAtTime(0.0001, zeit + dauer);
            ton.connect(lautstaerke);
            lautstaerke.connect(ctx.destination);
            ton.start(zeit);
            ton.stop(zeit + dauer + 0.02);
            zeit += dauer + pause;
        });
    }
    function vibriere(muster) {
        // iPhone-Browser koennen nicht vibrieren, dort bleibt Ton + Farbe.
        if (!navigator.vibrate) return false;
        try { return navigator.vibrate(muster); } catch (e) { return false; }
    }
    function signal(art) {
        piepe(SIGNALE[art].toene);
        vibriere(SIGNALE[art].vibration);
    }

    // ------------------------------------------------ Benachrichtigung
    const benachrichtigungMoeglich = () =>
        window.isSecureContext && 'serviceWorker' in navigator && 'Notification' in window;
    const erlaubnis = () => (benachrichtigungMoeglich() ? Notification.permission : 'unmoeglich');
    const benachrichtigungAn = () => einstellungen().benachrichtigung && erlaubnis() === 'granted';

    let swAktiv = null;
    function swBereit() {
        if (!benachrichtigungMoeglich()) return Promise.resolve(null);
        if (!swAktiv) {
            swAktiv = navigator.serviceWorker.register(SW_PFAD, { scope: '/static/js/' }).then(reg => {
                if (reg.active) return reg;
                const neu = reg.installing || reg.waiting;
                return new Promise(fertig => {
                    neu.addEventListener('statechange', () => { if (neu.state === 'activated') fertig(reg); });
                });
            }).catch(() => { swAktiv = null; return null; });
        }
        return swAktiv;
    }
    async function erlaubnisAnfragen() {
        if (!benachrichtigungMoeglich()) return false;
        if (Notification.permission === 'granted') return true;
        return (await Notification.requestPermission()) === 'granted';
    }

    // Web-Push: meldet dieses Handy beim Server an, damit die Meldungen auch
    // bei ausgeschaltetem Bildschirm kommen. courtId nur aus der Feldansicht
    // (fuer Geraete ohne Konto); auf "Mein Konto" bleibt es leer.
    function schluesselBytes(text) {
        const b64 = (text + '='.repeat((4 - text.length % 4) % 4)).replace(/-/g, '+').replace(/_/g, '/');
        return Uint8Array.from(atob(b64), z => z.charCodeAt(0));
    }
    async function aktuellesAbo() {
        const reg = await swBereit();
        return reg && reg.pushManager ? reg.pushManager.getSubscription().catch(() => null) : null;
    }
    async function pushAnmelden(schluessel, courtId) {
        const reg = schluessel ? await swBereit() : null;
        if (!reg || !reg.pushManager) return false;
        try {
            let abo = await reg.pushManager.getSubscription();
            if (!abo) {
                abo = await reg.pushManager.subscribe({ userVisibleOnly: true, applicationServerKey: schluesselBytes(schluessel) });
            }
            const antwort = await fetch('/push/abo', {
                method: 'POST',
                credentials: 'same-origin',
                headers: { 'Content-Type': 'application/json' },
                body: JSON.stringify({ abo: abo.toJSON(), court_id: courtId || null }),
            });
            return antwort.ok;
        } catch (e) {
            return false;
        }
    }
    async function pushAbmelden() {
        const abo = await aktuellesAbo();
        if (!abo) return;
        fetch('/push/abmelden', {
            method: 'POST',
            credentials: 'same-origin',
            headers: { 'Content-Type': 'application/json' },
            body: JSON.stringify({ endpoint: abo.endpoint }),
        }).catch(() => {});
        abo.unsubscribe().catch(() => {});
    }
    // Test-Push: der Server schickt eine Meldung an dieses Handy (sofort oder
    // nach `verzoegerung` Sekunden) und meldet zurueck, was passiert ist.
    async function testePush(verzoegerung) {
        const abo = await aktuellesAbo();
        if (!abo) return 'Dieses Handy ist nicht für Push angemeldet. Benachrichtigung aus- und wieder einschalten.';
        let ergebnis = null;
        try {
            const antwort = await fetch('/push/test', {
                method: 'POST',
                credentials: 'same-origin',
                headers: { 'Content-Type': 'application/json' },
                body: JSON.stringify({ endpoint: abo.endpoint, verzoegerung: verzoegerung || 0 }),
            });
            ergebnis = await antwort.json();
        } catch (e) { ergebnis = null; }
        const status = ergebnis ? ergebnis.status : null;
        if (ergebnis && ergebnis.ok && ergebnis.geplant) {
            return `Test-Push kommt in ${verzoegerung} Sekunden. Jetzt den Bildschirm ausschalten und auf Handy und Uhr achten.`;
        }
        if (ergebnis && ergebnis.ok) {
            return `Test-Push verschickt (Push-Dienst: ${status}). Kommt keine Meldung, liegt es an den Benachrichtigungs-Einstellungen von Handy oder Browser.`;
        }
        if (status === null || status === undefined) {
            return 'Dieses Handy ist beim Server nicht angemeldet. Benachrichtigung aus- und wieder einschalten.';
        }
        if (status === 0) return 'Der Server erreicht den Push-Dienst nicht (Internet/Firewall des Servers prüfen).';
        return `Der Push-Dienst hat abgelehnt (Status ${status}).`;
    }

    window.SportfestSignale = {
        einstellungen, speichere, mitKonto,
        SIGNALE, COUNTDOWN_SEKUNDEN, COUNTDOWN_VIBRATION,
        audioBereit, signal, vibriere,
        benachrichtigungMoeglich, erlaubnis, erlaubnisAnfragen, benachrichtigungAn,
        swBereit, aktuellesAbo, pushAnmelden, pushAbmelden, testePush,
    };
})();
