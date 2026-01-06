=================================
Pickings cancel and back to draft
=================================

Modified from Stock back 2 draft by OCA.
Original code: https://github.com/OCA/stock-logistics-workflow

.. |badge1| image:: https://img.shields.io/badge/licence-AGPL--3-blue.png
    :target: http://www.gnu.org/licenses/agpl-3.0-standalone.html
    :alt: License: AGPL-3

|badge1|

This module allows to cancel pickings and bring cancelled pickings back to draft.

**Table of contents**

.. contents::
   :local:

Installation
============

1. Copy the ``stock_picking_cancel_back2draft`` folder to your Odoo addons directory.
2. Update the apps list in Odoo (Settings > Apps > Update Apps List).
3. Search for "Pickings cancel and back to draft" and install it.

Configuration
=============

To allow users to cancel and bring pickings back to draft, you must add them to the
"Cancel & Back to Draft Pickings" security group:

1. Go to Settings > Users & Companies > Users.
2. Select the user you want to grant access to.
3. Under "Other" or "Technical Settings", enable "Cancel & Back to Draft Pickings".

Only users in this group will see the button and be able to use this feature.

Usage
=====

To cancel and bring a picking back to draft:

- In a pick form view, click on the 'Cancel & Back to Draft' button.
- In a pick list view, select the pickings and choose the 'Cancel & Back to Draft' action from the action menu.

**Note:** This action cannot be performed on pickings in the "done" state.

Running Tests
=============

To run the module tests in a Docker container:

.. code-block:: bash

    # Using docker exec (on a running container)
    docker exec -it 562803c922f2a3fb6652d0b1acce07c536dc60d18e308aba86192d791bb571c6 odoo \
  --addons-path=/opt/odoo/odoo/addons,/mnt/extra-addons \
  --test-enable --stop-after-init \
  -d odoo \
  -i stock_picking_cancel_back2draft \
  --log-level=test \
  --db_host=odoo-db-demo \
  --db_user=odoo \
  --db_password=odoo_demo_password

    # Using docker compose run (creates a new container)
    docker compose run --rm odoo odoo --test-enable --stop-after-init \
      -d odoo \
      -u stock_picking_cancel_back2draft \
      --log-level=test

Replace ``<container_name>`` with your Odoo container name and ``<database_name>`` with your database name.

Test flags:

- ``--test-enable`` - Enable test mode
- ``--stop-after-init`` - Stop after running tests
- ``-d`` - Database name
- ``-u`` - Update/test this module (use ``-i`` if not installed yet)
- ``--log-level=test`` - Show test output

Support
=======

For support, please contact SJR Nebula:

- Website: https://sjr.ie
- Email: info@sjr.ie

Credits
=======

Authors
-------

* SJR Nebula

Contributors
------------

* John Ashurst

Original Authors
----------------

* Agile Business Group

Original Contributors
---------------------

-  Lorenzo Battistini <lorenzo.battistini@agilebg.com>
-  Iván Montagud <ivan@studio73.es>
-  Pimolnat Suntian <pimolnats@ecosoft.co.th>
-  David Montull Guasch <david.montull@bt-group.com>
-  Marwan Behillil <marwan@riluxa.com>
