/* LocalDrop service worker.
   Caches only the fixed app shell; every /api/ call and file download stays
   live so transfers are never served from a stale cache. Written in ES5 to
   match the rest of the web folder. */
var CACHE = "localdrop-shell-v3";
var SHELL = ["/", "/app.css", "/app.js", "/icon.svg", "/manifest.json",
             "/icon-192.png", "/icon-512.png", "/apple-touch-icon.png"];

self.addEventListener("install", function (event) {
  event.waitUntil(caches.open(CACHE).then(function (cache) {
    return cache.addAll(SHELL);
  }));
  self.skipWaiting();
});

self.addEventListener("activate", function (event) {
  event.waitUntil(caches.keys().then(function (keys) {
    var stale = [];
    for (var i = 0; i < keys.length; i++) {
      if (keys[i] !== CACHE) { stale.push(caches.delete(keys[i])); }
    }
    return Promise.all(stale).then(function () { return self.clients.claim(); });
  }));
});

self.addEventListener("fetch", function (event) {
  if (event.request.method !== "GET") { return; }
  var path = new URL(event.request.url).pathname;
  if (SHELL.indexOf(path) < 0) { return; }
  event.respondWith(fetch(event.request).then(function (resp) {
    if (resp && resp.status === 200) {
      var copy = resp.clone();
      caches.open(CACHE).then(function (cache) { cache.put(event.request, copy); });
    }
    return resp;
  }).catch(function () {
    return caches.match(event.request).then(function (hit) {
      return hit || Response.error();
    });
  }));
});
