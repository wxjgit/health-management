const { createApi } = require("./api-core");
module.exports = createApi(require("./platform"), require("./environment"));
