/* Service Worker nur fuer lokale Benachrichtigungen der Feldansicht
   (/ergebnisse/feld): "Noch 1 Minute" und "Spielzeit vorbei", damit sie auch
   auf einer gekoppelten Smartwatch ankommen. Kein Push-Server, kein Cache,
   er steuert keine Seiten (Scope /static/js/). Die Seite ruft
   registration.showNotification() auf, solange sie offen ist. */
self.addEventListener('install', () => self.skipWaiting());

// Antippen der Benachrichtigung holt die Feldansicht nach vorn.
self.addEventListener('notificationclick', event => {
    event.notification.close();
    const ziel = (event.notification.data && event.notification.data.url) || '/ergebnisse/feld';
    event.waitUntil(
        self.clients.matchAll({ type: 'window', includeUncontrolled: true }).then(fenster => {
            const offen = fenster.find(f => f.url === ziel)
                || fenster.find(f => new URL(f.url).pathname.startsWith('/ergebnisse'));
            return offen ? offen.focus() : self.clients.openWindow(ziel);
        })
    );
});
