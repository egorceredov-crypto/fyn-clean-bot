const tg = window.Telegram.WebApp;

tg.ready();

tg.expand();

function startOrder() {

    tg.showPopup({
        title: "FYN Clean",
        message: "Оформление заказа сейчас откроется в боте.",
        buttons: [
            {
                id: "order",
                type: "default",
                text: "Заказать"
            },
            {
                type: "cancel"
            }
        ]
    }, function(buttonId) {

        if (buttonId === "order") {

            tg.close();

        }

    });

}