const CACHE_NAME = 'andal-star-core-v1.0';
const DYNAMIC_CACHE = 'andal-star-dynamic-v1.0';

// 1. التثبيت (Install) - تخزين هوية التطبيق الأساسية
self.addEventListener('install', (event) => {
    self.skipWaiting();
    event.waitUntil(
        caches.open(CACHE_NAME).then((cache) => {
            console.log('[Andal Star PWA] Core installed');
            return cache.addAll([
                '/static/manifest.json'
            ]);
        })
    );
});

// 2. التفعيل (Activate) - تنظيف أي كاش قديم لتوفير مساحة في هاتف الزبون
self.addEventListener('activate', (event) => {
    event.waitUntil(
        caches.keys().then((cacheNames) => {
            return Promise.all(
                cacheNames.map((cache) => {
                    if (cache !== CACHE_NAME && cache !== DYNAMIC_CACHE) {
                        console.log('[Andal Star PWA] Old cache cleared');
                        return caches.delete(cache);
                    }
                })
            );
        })
    );
    return self.clients.claim();
});

// 3. اعتراض الطلبات (Fetch) - استراتيجية "الشبكة أولاً مع الاحتياط بالكاش"
self.addEventListener('fetch', (event) => {
    // تجاهل الطلبات الخارجية
    if (!event.request.url.startsWith(self.location.origin)) return;

    event.respondWith(
        fetch(event.request).then((networkResponse) => {
            return caches.open(DYNAMIC_CACHE).then((cache) => {
                // تحديث الكاش بالبيانات الجديدة بصمت
                if (event.request.method === 'GET') {
                    cache.put(event.request, networkResponse.clone());
                }
                return networkResponse;
            });
        }).catch(() => {
            // في حال انقطاع الإنترنت، عرض آخر نسخة مسجلة في هاتف الزبون
            return caches.match(event.request);
        })
    );
});

// 4. استلام إشعارات الهاتف (Push Notifications)
self.addEventListener('push', (event) => {
    // إعدادات الإشعار الافتراضية
    let data = {
        title: 'عندل ستار VIP ⌬',
        body: 'لديك إشعار جديد من النظام!',
        url: '/notifications'
    };

    if (event.data) {
        try {
            // معالجة البيانات إذا كانت بصيغة JSON
            data = event.data.json();
        } catch (e) {
            // نظام حماية: إذا أرسل السيرفر نصاً عادياً يتم اعتماده مباشرة بدون انهيار الكود
            data = {
                title: 'عندل ستار VIP ⌬',
                body: event.data.text(),
                url: '/notifications'
            };
        }
    }

    const options = {
        body: data.body,
        icon: '/static/icon-192.png',        // تم التفعيل وتعديل المسار المباشر للشعار
        badge: '/static/icon-192.png',       // الأيقونة الصغيرة المخصصة لشريط إشعارات الهاتف العلوي
        vibrate: [200, 100, 200, 100, 200],  // نمط الاهتزاز الملكي المتناسق
        data: { url: data.url },
        dir: 'rtl'
    };

    event.waitUntil(
        self.registration.showNotification(data.title, options)
    );
});

// 5. التفاعل عند الضغط على الإشعار من شاشة الهاتف
self.addEventListener('notificationclick', (event) => {
    event.notification.close();
    event.waitUntil(
        clients.matchAll({ type: 'window' }).then((windowClients) => {
            // إذا كان التطبيق مفتوحاً في الخلفية، قم بجلبه للأمام
            for (let i = 0; i < windowClients.length; i++) {
                let client = windowClients[i];
                if (client.url.includes(event.notification.data.url) && 'focus' in client) {
                    return client.focus();
                }
            }
            // إذا كان التطبيق مغلقاً تماماً، افتحه على صفحة الإشعار مباشرة
            if (clients.openWindow) {
                return clients.openWindow(event.notification.data.url);
            }
        })
    );
});

