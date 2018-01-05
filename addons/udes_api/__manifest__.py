# -*- coding: utf-8 -*-
{
    'name': "UDES API",

    'summary': """
        UDES API""",

    'description': """
        A set of API end points for getting data in and out of Odoo 
    """,

    'author': "Unipart Digital Team",
    'website': "http://www.unipart.io",

    'category': 'API',
    'version': '11.0',

    # any module necessary for this one to work correctly
    'depends': [
        'stock',
        'stock_picking_batch',
        'warehouse_config',
        'blocked_locations',
        'udes_core',
    ],

    # always loaded
    'data': [
    ],
    'demo': [
    ],
}
