/*Run this on the console of a walmart order history page*/
(function copyBill() {
    let total = document.querySelector("div.bill-order-total-payment").textContent.slice(6)
    let items = document.querySelectorAll("a[link-identifier='itemClick']")
    let qts = document.querySelectorAll("div.bill-item-quantity")
    let prices = document.querySelectorAll("div.column3")
    let imgs = document.querySelectorAll("img[data-testid='productTileImage']")
    if (items.length != qts.length || qts.length != prices.length || prices.length != imgs.length) {
        console.error(`Error: different number of items ${items.length} qts ${qts.length} prices ${prices.length} imgs ${imgs.length}`)
        return
    }
    let price_regex = /\$\d+(\.\d+)?/
    final = Array.from(items).map((it, i) => ({
        name: it.textContent,
        qty: qts[i].textContent.slice(4),
        price: prices[i].firstChild.textContent.match(price_regex)[0].slice(1),
        unavailable: imgs[i].classList.contains("o-30"),
    }))
    .filter(it => !it.unavailable)
    res = final.map(f => `${f.qty}\t${f.name}\t${f.price}`).join('\n')
    copy(`!paid: ${total}\n!paid-by: krut\n\n${res}\n`)
})()
