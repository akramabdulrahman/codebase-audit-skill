const express = require('express');
const router = express.Router();
const authentication = require('../../middlewares/authentication');
const { manageRoles } = require('../../constants');
const getWidget = require('./getWidget');
const updateWidget = require('./updateWidget');
const deleteWidget = require('./deleteWidget');
router.get('/:id', authentication({ allowPublic: true }), getWidget);
router.patch('/:id', authentication({ allowedRoles: manageRoles }), updateWidget);   // GUARDED handler
router.delete('/:id', authentication({ allowedRoles: manageRoles }), deleteWidget);  // UNGUARDED handler -> IDOR
module.exports = router;
