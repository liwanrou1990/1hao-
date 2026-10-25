// SPDX-License-Identifier: MIT
pragma solidity ^0.8.28;

interface ITWAP { function consult() external view returns (uint256); }

interface IToken {
    function transfer(address to, uint256 amount) external returns (bool);
    function transferFrom(address from, address to, uint256 amount) external returns (bool);
    function balanceOf(address) external view returns (uint256);
}

/// @title SafeOracleVault  (REMEDIATED)
/// @notice Fixes [VULN-5] by valuing collateral with a manipulation-resistant
///         TWAP instead of the instantaneous spot price.
contract SafeOracleVault {
    ITWAP public immutable oracle;
    IToken public immutable collateral;
    IToken public immutable payout;
    uint256 public constant LTV_BPS = 8000;

    mapping(address => uint256) public collateralOf;
    mapping(address => uint256) public debtOf;

    constructor(ITWAP _oracle, IToken _collateral, IToken _payout) {
        oracle = _oracle;
        collateral = _collateral;
        payout = _payout;
    }

    function deposit(uint256 amount) external {
        collateral.transferFrom(msg.sender, address(this), amount);
        collateralOf[msg.sender] += amount;
    }

    function maxBorrow(address user) public view returns (uint256) {
        uint256 price = oracle.consult(); // [FIX-5] TWAP, not spot
        uint256 value = (collateralOf[user] * price) / 1e18;
        return (value * LTV_BPS) / 10000;
    }

    function borrow(uint256 amount) external {
        require(debtOf[msg.sender] + amount <= maxBorrow(msg.sender), "undercollateralized");
        debtOf[msg.sender] += amount;
        require(payout.transfer(msg.sender, amount), "payout");
    }
}
