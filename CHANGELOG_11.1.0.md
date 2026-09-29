# 11.1.0

- Убран ложный универсальный API checkout как основной механизм.
- Добавлен поставщик-специфичный Supplier Gateway.
- Реальный ROSSKO checkout через SOAP GetCheckout.
- Реальный AVD checkout через InsertToBasket/CreateOrder.
- Реальный AutoEuro и MParts/v01 checkout через orders/instant.
- В корзину передаются идентификаторы конкретного предложения поставщика.
- Исправлено сохранение поставщика: ошибка тестового API больше не удаляет введённые данные.
- Для неподтверждённых/закрытых API используется реальный SMTP fallback, а не выдуманный endpoint.
- Добавлена матрица интеграций SUPPLIER_API_MATRIX.md.
