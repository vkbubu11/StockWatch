"""Hir- es hangulatelemzes (sentiment) - terv: docs/SENTIMENT-DESIGN.md, ticket #20.

ARNYEK-UZEM: a hir-jellemzok a jelzes melle kerulnek (szuro "Hirek" - nem dont, a naploba es az
ertesitesbe bekerul), de a jelzes kimenetelet NEM valtoztatjak, amig a validacio (2. fazis) nem
mutat merheto hatast.

  model.py      Article (a megjelenes UTC idopontjaval)
  scoring.py    pontozas: VADER + penzugyi szolista + esemenyfelismeres (2 reteg egyetertese kell)
  sources/      edgar (SEC 8-K, ingyenes, tortenti), yahoo (friss hirek), alpaca (Benzinga, kulccsal)
  store.py      data/news/<PAPIR>.json.gz - cikkek, lefedettsegi idoszakok, lemezplafon
  features.py   napi, POINT-IN-TIME jellemzok (a t napi dontes csak a 16:00 NY elott megjelent hireket latja)
  service.py    frissites, a jelzesek kiegeszitese (annotate), szoveg az ertesiteshez
"""
