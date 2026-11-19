       IDENTIFICATION DIVISION.
       PROGRAM-ID. PAYROLL.
       AUTHOR. LEGACY-BANKING-TEAM.
      ******************************************************************
      * CALCULO DE LIQUIDACION DE NOMINA Y RETENCION IMPOSITIVA
      ******************************************************************
       DATA DIVISION.
       WORKING-STORAGE SECTION.
       COPY ACCOUNT.
       01  WS-WORK-AREAS.
           05  WS-GROSS-PAY          PIC 9(07)V99 VALUE ZERO.
           05  WS-TAX-AMOUNT         PIC 9(07)V99 VALUE ZERO.
           05  WS-SENIORITY-YEARS    PIC 9(02)    VALUE 6.
           05  WS-LOYALTY-BONUS      PIC 9(05)V99 VALUE 250.00.

       PROCEDURE DIVISION.
       MAIN-PROCESS SECTION.
       0000-START.
           MOVE 10004512         TO ACCT-ID.
           MOVE "CARLOS MENDOZA" TO ACCT-HOLDER-NAME.
           MOVE "A"              TO ACCT-STATUS.
           MOVE 4500.00          TO ACCT-BASE-SALARY.
           MOVE 300.00           TO ACCT-BONUS-AMT.
           MOVE 13.00            TO ACCT-TAX-RATE.
           PERFORM 1000-CALCULATE-PAY.
           CALL "INTEREST" USING ACCT-ID ACCT-NET-PAY.
           DISPLAY "EMPLEADO: " ACCT-HOLDER-NAME " NETO: " ACCT-NET-PAY.
           STOP RUN.

       1000-CALCULATE-PAY SECTION.
       1010-COMPUTE.
      *    TODO: Simplificar asignacion redundante ADD ... TO 0 GIVING
           ADD ACCT-BASE-SALARY TO 0 GIVING WS-GROSS-PAY.
           IF WS-SENIORITY-YEARS > 5
               COMPUTE WS-GROSS-PAY = (WS-GROSS-PAY + WS-LOYALTY-BONUS)
           END-IF.
           COMPUTE WS-GROSS-PAY = (WS-GROSS-PAY + ACCT-BONUS-AMT).
           COMPUTE WS-TAX-AMOUNT =
               (WS-GROSS-PAY * ACCT-TAX-RATE) / 100.
           COMPUTE ACCT-NET-PAY = (WS-GROSS-PAY - WS-TAX-AMOUNT).
       1099-EXIT.
           EXIT.
