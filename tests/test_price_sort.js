const fs=require('fs'),vm=require('vm'),assert=require('assert');
const html=fs.readFileSync('/mnt/data/index_price_sort_fixed.html','utf8');
const fn=html.slice(html.indexOf('function sortedFilteredListings(){'),html.indexOf('\nfunction listingsTable(){'));
const fields={'listing-filter':{value:''},'listing-currency':{value:'all'},'listing-sort':{value:'default'},'price-sort':{value:'none'}};
const context={state:{data:{listings:[
{id:1,title:'A',shop_name:'One',currency:'USD',price:25,shop_review_count:11},
{id:2,title:'B',shop_name:'Two',currency:'EUR',price:8,shop_review_count:50},
{id:3,title:'C',shop_name:'Three',currency:'USD',price:4,shop_review_count:90},
{id:4,title:'D',shop_name:'Four',currency:'EUR',price:30,shop_review_count:4},
{id:5,title:'E',shop_name:'Five',currency:'USD',price:null,shop_review_count:null},
{id:6,title:'F',shop_name:'Six',currency:'USD',price:4,shop_review_count:20}
]}},$:k=>fields[k],numeric:x=>typeof x==='number'&&Number.isFinite(x)?x:null,currency:x=>/^[A-Z]{3}$/.test(x||'')?x:'Unknown',safeString:x=>typeof x==='string'?x:''};
vm.createContext(context);vm.runInContext(fn,context);const got=()=>Array.from(context.sortedFilteredListings(),x=>x.id).join(',');
function check(name,options,expected){for(const [k,v] of Object.entries(options))fields[k].value=v;assert.strictEqual(got(),expected,name);console.log('PASS',name)}
check('default order',{'listing-currency':'all','price-sort':'none','listing-sort':'default','listing-filter':''},'1,2,3,4,5,6');
check('USD ascending',{'listing-currency':'USD','price-sort':'asc'},'3,6,1,5');
check('USD descending',{'price-sort':'desc'},'1,3,6,5');
check('Global grouped ascending',{'listing-currency':'all','price-sort':'asc'},'2,4,3,6,1,5');
check('Global grouped descending',{'price-sort':'desc'},'4,2,1,3,6,5');
check('Shop reviews descending',{'price-sort':'none','listing-sort':'reviews'},'3,2,6,1,4,5');
check('Search + currency + price',{'listing-filter':'Three','listing-currency':'USD','price-sort':'asc','listing-sort':'default'},'3');
check('No numeric prices / stable ties',{'listing-filter':'Five','listing-currency':'all','price-sort':'desc'},'5');
console.log('8/8 tests passed');
