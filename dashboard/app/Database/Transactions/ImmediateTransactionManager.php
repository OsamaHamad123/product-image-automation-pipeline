<?php

namespace App\Database\Transactions;

use Illuminate\Support\Facades\DB;
use Closure;
use Throwable;

class ImmediateTransactionManager
{
    /**
     * ينفذ المعاملة بصيغة BEGIN IMMEDIATE TRANSACTION لحماية قواعد البيانات ومنع الجمود.
     *
     * @template T
     * @param Closure(): T $callback
     * @return T
     * @throws Throwable
     */
    public static function execute(Closure $callback)
    {
        $connection = DB::connection();
        $driver = $connection->getDriverName();

        if ($driver === 'sqlite') {
            $connection->statement('BEGIN IMMEDIATE TRANSACTION');
            try {
                $result = $callback();
                $connection->statement('COMMIT');
                return $result;
            } catch (Throwable $e) {
                $connection->statement('ROLLBACK');
                throw $e;
            }
        }

        return DB::transaction($callback);
    }
}
