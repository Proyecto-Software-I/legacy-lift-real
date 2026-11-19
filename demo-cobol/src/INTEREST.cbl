       IDENTIFICATION DIVISION.
       PROGRAM-ID. INTEREST.
       AUTHOR. LEGACY-BANKING-TEAM.
      ******************************************************************
      * SUBPROGRAMA DE INTERESES MENSUALES SOBRE SALDO DE NOMINA
      ******************************************************************
       DATA DIVISION.
       WORKING-STORAGE SECTION.
       01  WS-INTEREST-VARS.
           05  WS-ANNUAL-RATE        PIC 9(02)V99 VALUE 04.50.
           05  WS-MONTHLY-INTEREST   PIC 9(07)V99 VALUE ZERO.
           05  WS-UPDATED-BALANCE    PIC 9(07)V99 VALUE ZERO.

       LINKAGE SECTION.
       01  LK-ACCT-ID                PIC 9(08).
       01  LK-BALANCE                PIC 9(07)V99.

       PROCEDURE DIVISION USING LK-ACCT-ID LK-BALANCE.
       CALC-INTEREST SECTION.
       2000-APPLY-RATE.
      *    TODO: Refactorizar expresion con parentesis redundantes
           ADD LK-BALANCE TO 0 GIVING WS-UPDATED-BALANCE.
           COMPUTE WS-MONTHLY-INTEREST =
               ((WS-UPDATED-BALANCE * WS-ANNUAL-RATE) / 1200).
           COMPUTE LK-BALANCE =
               (WS-UPDATED-BALANCE + WS-MONTHLY-INTEREST).
           GOBACK.
