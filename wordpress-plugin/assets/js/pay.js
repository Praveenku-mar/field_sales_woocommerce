/* Agamsale Razorpay: the order's pay page. Opens Razorpay Checkout and posts Razorpay's
 * answer (payment id, order id, signature) to the store, which has Agamsale verify it. */
(function () {
	'use strict';
	var data = window.agamsaleRazorpay;
	var form = document.getElementById('agamsale-razorpay-form');
	var button = document.getElementById('agamsale-razorpay-pay');
	var message = document.getElementById('agamsale-razorpay-message');
	if (!data || !form || !button || typeof window.Razorpay !== 'function') {
		return;
	}

	function say(text, isError) {
		message.textContent = text;
		message.className = isError ? 'woocommerce-error' : 'woocommerce-info';
	}

	function open() {
		button.disabled = true;
		var checkout = new window.Razorpay({
			key: data.key,
			order_id: data.orderId,
			amount: data.amount,
			currency: data.currency,
			name: data.name,
			description: data.description,
			prefill: data.prefill || {},
			theme: { color: '#2271b1' },
			handler: function (response) {
				say(data.messages.processing, false);
				form.elements.razorpay_payment_id.value = response.razorpay_payment_id || '';
				form.elements.razorpay_order_id.value = response.razorpay_order_id || '';
				form.elements.razorpay_signature.value = response.razorpay_signature || '';
				form.submit();
			},
			modal: {
				// The customer closed the window: nothing is charged; the order waits for payment.
				ondismiss: function () {
					button.disabled = false;
					say(data.messages.cancelled, false);
				},
			},
		});
		checkout.on('payment.failed', function (response) {
			var reason = (response && response.error && response.error.description) || '';
			button.disabled = false;
			say(data.messages.failed + reason, true);
		});
		checkout.open();
	}

	button.addEventListener('click', open);
	if (data.autoOpen) {
		open();
	}
})();
