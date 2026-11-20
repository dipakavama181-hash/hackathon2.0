import os
import json
from typing import List


def add_item(item, items=[]):
    items.append(item)
    return items


def total(prices, discount=0):
    subtotal = sum(prices)
    return subtotal * discount


def safe_div(a, b):
    try:
        return a / b
    except:
        return None


def is_empty(x):
    if x == None:
        return True
    return False


def run(expr):
    return eval(expr)
