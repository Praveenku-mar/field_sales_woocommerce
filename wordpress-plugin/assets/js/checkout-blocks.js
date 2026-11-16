/* Agamsale Razorpay on the Checkout block: registers the payment option. Placing the order
 * goes to the order's pay page, where Razorpay Checkout opens (assets/js/pay.js). */
(function () {
	'use strict';
	var registry = window.wc && window.wc.wcBlocksRegistry;
	var wcSettings = window.wc && window.wc.wcSettings;
	if (!registry || !wcSettings || !window.wp || !window.wp.element) {
		return;
	}
	var el = window.wp.element.createElement;
	var decode = (window.wp.htmlEntities && window.wp.htmlEntities.decodeEntities) || function (s) { return s; };
	var settings = wcSettings.getSetting('agamsale_razorpay_data', {});
	var title = decode(settings.title || 'Razorpay');

	function Content() {
		return el('div', null, decode(settings.description || ''));
	}

	registry.registerPaymentMethod({
		name: 'agamsale_razorpay',
		label: el('span', null, title),
		ariaLabel: title,
		content: el(Content),
		edit: el(Content),
		canMakePayment: function () {
			return true;
		},
		supports: { features: settings.supports || ['products'] },
	});
})();
