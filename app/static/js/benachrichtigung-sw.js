/* Service Worker nur fuer lokale Benachrichtigungen der Feldansicht
   (/ergebnisse/feld): "Noch 1 Minute" und "Spielzeit vorbei", damit sie auch
   auf einer gekoppelten Smartwatch ankommen. Kein Cache, er steuert keine
   Seiten (Scope /static/js/). Ist der Bildschirm an, zeigt die Seite die
   Meldung selbst (registration.showNotification). Bei ausgeschaltetem
   Bildschirm ist die Seite angehalten; dann schickt der Server die Meldung
   per Web-Push (app/services/push_service.py) und dieser Worker zeigt sie. */
self.addEventListener('install', () => self.skipWaiting());

self.addEventListener('push', event => {
    let daten = {};
    try { daten = event.data ? event.data.json() : {}; } catch (e) { daten = {}; }
    event.waitUntil(
        self.clients.matchAll({ type: 'window', includeUncontrolled: true }).then(fenster => {
            // Hat die offene, sichtbare Seite schon selbst gemeldet (gleicher
            // tag), wird nur still ersetzt statt ein zweites Mal vibriert.
            const sichtbar = fenster.some(f => f.visibilityState === 'visible'
                && new URL(f.url).pathname.startsWith('/ergebnisse/feld'));
            return self.registration.showNotification(daten.titel || 'Sportfest', {
                body: daten.text || '',
                tag: daten.tag,
                renotify: !sichtbar && !!daten.tag,
                data: { url: daten.url || '/ergebnisse/feld' },
            });
        })
    );
});

// Antippen der Benachrichtigung holt die Feldansicht nach vorn.
self.addEventListener('notificationclick', event => {
    event.notification.close();
    const ziel = new URL((event.notification.data && event.notification.data.url) || '/ergebnisse/feld', self.location.origin).href;
    event.waitUntil(
        self.clients.matchAll({ type: 'window', includeUncontrolled: true }).then(fenster => {
            const offen = fenster.find(f => f.url === ziel)
                || fenster.find(f => new URL(f.url).pathname.startsWith('/ergebnisse'));
            return offen ? offen.focus() : self.clients.openWindow(ziel);
        })
    );
});
