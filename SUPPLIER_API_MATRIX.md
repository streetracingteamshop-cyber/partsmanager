# Parts Manager 11.1.0 — Supplier Integration Matrix

## Реальные checkout-адаптеры

- ROSSKO: SOAP API v2.1 — GetSearch, GetCheckoutDetails, GetCheckout, GetOrders. Для checkout используются реальные `stock`, `delivery_id`, `address_id`, `payment_id`, `requisite_id` из API. Тестовый режим ROSSKO должен быть включён отдельно в кабинете поставщика.
- AVD Motors: SOAP/WCF — GetOriginalPrice/GetFastCrossesPrice → Hash → InsertToBasket → CreateOrder → GetOrders. Доступ требует регистрации IP клиента у AVD.
- AutoEuro: REST API v2 — search_items → create_order → get_orders. В корзине сохраняется offer_key; при заказе передаются delivery_key и payer_key.
- MParts/v01: REST — search/articles → basket/actualize* при необходимости → orders/instant (или basket/order) → orders/list. Используются `supplierCode`, `itemKey`/`code`, payment/shipment parameters.

## E-mail checkout

Для поставщиков без подтверждённого публичного checkout API в этой сборке используется настоящий SMTP-заказ. Это не эмуляция: письмо отправляется на заданный адрес поставщика, сохраняется Message-ID, а при наличии последнего прайса используется In-Reply-To/References.

## Важно

Для BERG, Armtek, Autopiter, Autotrade, Forum-Auto, PartKom, Autorus, Exist, Emex, EuroAuto, Autospunik, STparts, Shate-M, Auto Alliance, Carreta, Brinex, ABSTD/ABS-Auto и других поставщиков, где checkout-контракт зависит от B2B-договора/закрытых credentials либо публичная документация не подтверждает покупательский checkout, приложение НЕ выдумывает endpoint. После предоставления поставщиком официального URL/контракта его можно подключить отдельным адаптером; до этого автоматический реальный канал — e-mail.

## Источники, проверенные при выпуске

- ROSSKO API: https://api.rossko.ru/
- AutoEuro API v2: https://api.autoeuro.ru/doc/v2
- AVD Motors Web Service: https://www.avdmotors.ru/p/web-service-pokupatelya
- MParts/v01 API: https://v01.ru/api/devinsight/documentation/
- Autopiter seller API: https://seller.autopiter.ru/index.html (это API продавца торговой площадки, поэтому оно не использовано как покупательский checkout без соответствующего контракта).
