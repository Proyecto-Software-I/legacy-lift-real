      ******************************************************************
      * COPYBOOK: ACCOUNT.CPY - REGISTRO DE CUENTA Y NOMINA LEGACY
      ******************************************************************
       01  ACCOUNT-RECORD.
           05  ACCT-ID               PIC 9(08).
           05  ACCT-HOLDER-NAME      PIC X(30).
           05  ACCT-STATUS           PIC X(01).
               88  ACCT-ACTIVE       VALUE 'A'.
               88  ACCT-FROZEN       VALUE 'F'.
               88  ACCT-CLOSED       VALUE 'C'.
           05  ACCT-BASE-SALARY      PIC 9(07)V99.
           05  ACCT-BONUS-AMT        PIC 9(05)V99.
           05  ACCT-TAX-RATE         PIC 9(02)V99.
           05  ACCT-NET-PAY          PIC 9(07)V99.
