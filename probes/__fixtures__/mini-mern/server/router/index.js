const express = require('express');
const router = express.Router();
const authentication = require('../middlewares/authentication');
const { manageRoles, userRoles } = require('../constants');
const widgetRouter = require('../controllers/widget');
const updateSelf = require('../controllers/users/updateSelf');
const login = require('../controllers/users/login');

router.post('/login', login);                                         // open (no auth)
router.patch('/me', authentication(), updateSelf);                    // authed-any, self-service
router.use('/widgets', widgetRouter);                                 // sub-router mount
module.exports = router;
